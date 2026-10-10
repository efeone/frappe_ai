"""HTTP API for FrappeForge (see frappe_ai/frappeforge/__init__.py and docs/FRAPPEFORGE.md).

Called by the FrappeForge agent (with its connection's API key):
  status, propose_release, request_rollback
Called from Desk by a System Manager:
  create_connection, rotate_key, revoke, approve, reject, rollback

The guard (frappe_ai.frappeforge.guard) refuses an agent anything not listed for it above.
"""

import json

import frappe
from frappe import _

from frappe_ai.frappeforge import connection, deploy

#: Per connection, not per IP: every FrappeForge sandbox leaves through the same egress addresses.
PROPOSALS_PER_HOUR = 30
ROLLBACK_REQUESTS_PER_HOUR = 60


# ── agent ─────────────────────────────────────────────────────────────────────


@frappe.whitelist(methods=["GET", "POST"])
def status() -> dict:
	return deploy.status()


@frappe.whitelist(methods=["POST"])
def propose_release(
	summary: str = "", conversation_id: str = "", idempotency_key: str = "", extra_records: str | None = None
) -> dict:
	"""Multipart upload: the release tarball in the ``release`` field."""
	_limit("propose", PROPOSALS_PER_HOUR)
	upload = frappe.request.files.get("release") if frappe.request and frappe.request.files else None
	if not upload:
		frappe.throw(_("Send the release tarball in the 'release' field."))
	return deploy.propose(
		upload.stream.read(),
		upload.filename or "",
		summary=summary,
		conversation_id=conversation_id,
		idempotency_key=idempotency_key,
		extra_records=json.loads(extra_records) if extra_records else None,
	)


@frappe.whitelist(methods=["POST"])
def request_rollback(change: str) -> dict:
	_limit("request_rollback", ROLLBACK_REQUESTS_PER_HOUR)
	deploy.request_rollback(change)
	return {"ok": True}


# ── site owner ────────────────────────────────────────────────────────────────


@frappe.whitelist(methods=["POST"])
def create_connection(
	label: str, expires_in_days: int = 30, allowed_ips: str = "", read_roles: str = ""
) -> dict:
	frappe.only_for("System Manager")
	out = connection.create_connection(label, int(expires_in_days), allowed_ips, read_roles)
	out["snippet"] = _snippet(out)
	return out


@frappe.whitelist(methods=["POST"])
def rotate_key(connection_name: str) -> dict:
	frappe.only_for("System Manager")
	out = connection.rotate_key(connection_name)
	out["site_url"] = frappe.utils.get_url()
	out["snippet"] = _snippet(out)
	return out


@frappe.whitelist(methods=["POST"])
def revoke(connection_name: str) -> dict:
	frappe.only_for("System Manager")
	connection.revoke(connection_name)
	return {"ok": True}


@frappe.whitelist(methods=["POST"])
def approve(release: str) -> dict:
	frappe.only_for("System Manager")
	return deploy.approve(release)


@frappe.whitelist(methods=["POST"])
def reject(release: str, reason: str = "") -> dict:
	frappe.only_for("System Manager")
	deploy.reject(release, reason)
	return {"ok": True}


@frappe.whitelist(methods=["POST"])
def rollback(change: str) -> dict:
	frappe.only_for("System Manager")
	return {"rollback": deploy.rollback(change)}


def _snippet(out: dict) -> str:
	"""What the System Manager pastes into FrappeForge's Connect live site dialog. Includes the
	MCP Server URL from AI Assistant Settings when one is set."""
	if mcp_url := frappe.db.get_single_value("AI Assistant Settings", "mcp_url"):
		out["mcp_url"] = mcp_url
	keys = ("site_url", "api_key", "api_secret", "access_token", "mcp_url")
	return json.dumps({k: out[k] for k in keys if out.get(k)})


def _limit(action: str, per_hour: int) -> None:
	"""Fixed one-hour window per (user, action) in Redis; one INCR per call."""
	key = frappe.cache.make_key(f"frappe_ai:frappeforge:rl:{action}:{frappe.session.user}")
	count = frappe.cache.incr(key)
	if count == 1:
		frappe.cache.expire(key, 3600)
	if count > per_hour:
		raise frappe.TooManyRequestsError(
			_("Too many requests from this FrappeForge connection; try again later.")
		)
