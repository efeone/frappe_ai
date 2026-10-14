"""Request guard for Vyogo Forge AI agent users (``auth_hooks``).

Frappe calls ``auth_hooks`` inside ``validate_auth`` right after an API key or bearer token has
set the session user, which is the first point the user is known (``before_request`` runs
earlier). For an agent user this hook allows only:

- reads: ``GET``/``HEAD`` on ``/api/resource/<doctype>[/<name>]``, ``/api/v2/document/...`` and
  ``/api/v2/doctype/<doctype>/meta``, and calls of read-only methods (``READ_METHODS``);
- the agent's own endpoints in ``frappe_ai.api.frappeforge`` (``AGENT_METHODS``).

Everything else is a 403, whatever the agent user's roles allow. Ordinary users pay one string
comparison per request.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime

from frappe_ai.frappeforge import connection

READ_METHODS = frozenset(
	{
		"ping",
		"frappe.ping",
		"frappe.auth.get_logged_user",
		"frappe.client.get",
		"frappe.client.get_list",
		"frappe.client.get_count",
		"frappe.client.get_value",
		"frappe.client.get_single_value",
		"frappe.client.validate_link",
		"frappe.desk.reportview.get",
		"frappe.desk.reportview.get_list",
		"frappe.desk.reportview.get_count",
		"frappe.desk.form.load.getdoc",
		"frappe.desk.form.load.getdoctype",
		"frappe.desk.search.search_link",
		"frappe.desk.search.search_widget",
		"frappe.desk.query_report.run",
		"frappe.desk.query_report.get_script",
		"frappe.utils.global_search.search",
		# the hosted frappe-mcp-server checks the Bearer token here before forwarding it
		"frappe.integrations.oauth2.openid_profile",
	}
)
AGENT_METHODS = frozenset(
	{
		"frappe_ai.api.frappeforge.status",
		"frappe_ai.api.frappeforge.propose_release",
		"frappe_ai.api.frappeforge.request_rollback",
	}
)
_READ_VERBS = frozenset({"GET", "HEAD"})


def decide(
	method: str, path: str, cmd: str | None = None, extra: list[str] | None = None
) -> tuple[bool, str]:
	"""Whether an agent user may make this request, and why not."""
	method = (method or "").upper()
	allowed_methods = READ_METHODS | AGENT_METHODS | frozenset(extra or ())
	if cmd:
		return _method_call(cmd, allowed_methods)

	parts = [p for p in (path or "").split("/") if p]
	if parts[:1] != ["api"]:
		return False, "only the REST API is open to Vyogo Forge AI"
	rest = parts[1:]
	if rest[:1] == ["v2"]:
		rest = rest[1:]
		if rest[:1] == ["document"] and 2 <= len(rest) <= 3:
			return _read_verb(method)
		if rest[:1] == ["doctype"] and len(rest) == 3 and rest[2] == "meta":
			return _read_verb(method)
	elif rest[:1] == ["resource"] and 2 <= len(rest) <= 3:
		return _read_verb(method)
	if rest[:1] == ["method"] and len(rest) == 2:
		return _method_call(rest[1], allowed_methods)
	return False, "this endpoint is not open to Vyogo Forge AI"


def enforce() -> None:
	"""``auth_hooks`` entry point."""
	user = frappe.session.user
	if not connection.is_agent_user(user):
		return
	now = now_datetime()
	conn = connection.agent_connection(user)
	if conn is None or not connection.is_usable(conn, now):
		raise frappe.PermissionError(_("This Vyogo Forge AI connection is revoked or expired."))
	request = frappe.request
	allowed, reason = decide(
		request.method,
		request.path,
		(frappe.form_dict or {}).get("cmd") if request.path in ("", "/") else None,
		frappe.local.conf.get("frappeforge_read_methods") or [],
	)
	if not allowed:
		frappe.logger("frappe_ai.frappeforge").warning(
			"refused %s %s for %s: %s", request.method, request.path, conn["name"], reason
		)
		raise frappe.PermissionError(_("Vyogo Forge AI may only read and propose changes: {0}.").format(reason))
	connection.touch(conn, now)


def _read_verb(method: str) -> tuple[bool, str]:
	if method in _READ_VERBS:
		return True, ""
	return False, f"{method} is a write; Vyogo Forge AI proposes changes instead"


def _method_call(name: str, allowed: frozenset) -> tuple[bool, str]:
	if name in allowed:
		return True, ""
	return False, f"{name} is not a read-only method"
