# Copyright (c) 2026, Vyogo and contributors
# For license information, please see license.txt

"""The user's say over a write the assistant wants to make.

The agent stops a turn on any tool that changes data and sends the sidebar a
``tool_confirm`` chunk; nothing has run yet. The write happens only on a later
turn that carries a one-time token minted here, and frappe-mcp-server spends
that token against ``redeem`` (as the same user) before it touches Frappe.

Who mints a token is a policy question, answered by ``decide``:

- ``ask``    the sidebar shows an Allow / Deny card; Allow mints the token
             (``chat.start_stream`` with a ``confirmation``).
- ``allow``  the site, or the user for this conversation, has said writes of
             this kind need no click; the relay mints the token itself and runs
             the next turn straight away.
- ``deny``   the site is read-only for the assistant; no token is ever minted.

Nothing the model writes can mint a token: ``issue`` is not whitelisted, and
the whitelisted ``redeem`` can only spend one.
"""

from __future__ import annotations

import json
import secrets

import frappe
from frappe import _

MODE_ASK = "Ask every time"
MODE_AUTO_EDITS = "Auto-approve creates and updates"
MODE_AUTO_ALL = "Auto-approve all changes"
MODE_READ_ONLY = "Read only"
MODES = (MODE_ASK, MODE_AUTO_EDITS, MODE_AUTO_ALL, MODE_READ_ONLY)

# A write that cannot be undone is never covered by a blanket "yes" short of the
# site-wide "Auto-approve all changes".
DESTRUCTIVE_TOOLS = frozenset({"delete_document"})
ALWAYS_ASK_DOCTYPES = frozenset(
	{"Client Script", "User", "Role Profile", "User Permission", "Custom DocPerm"}
)

_TOKEN_KEY_PREFIX = "frappe_ai:confirm:token:"
# Long enough for the relay to pick the token up and the agent to make the call;
# short enough that a token left behind by a failed turn cannot be found later.
_TOKEN_TTL_SECONDS = 600

_SESSION_KEY_PREFIX = "frappe_ai:confirm:session:"
_SESSION_TTL_SECONDS = 4 * 60 * 60

_TOOL_MAX_LEN = 200
_ARGUMENTS_MAX_BYTES = 64 * 1024


def _logger():
	return frappe.logger("frappe_ai", allow_site=True)


def _text(value) -> str:
	return value.strip() if isinstance(value, str) else ""


# ──────────────────────────────────────────────────────────────────────────
# Policy
# ──────────────────────────────────────────────────────────────────────────


def site_mode() -> str:
	"""The site-wide answer to "may the assistant change data without asking?"."""
	mode = frappe.db.get_single_value("AI Assistant Settings", "write_approval")
	return mode if mode in MODES else MODE_ASK


def session_auto_approve_allowed() -> bool:
	"""Whether a user may say "allow all in this conversation" from the card."""
	return bool(frappe.db.get_single_value("AI Assistant Settings", "allow_session_auto_approve"))


def _session_key(user: str, session_id: str) -> str:
	return f"{_SESSION_KEY_PREFIX}{user}:{session_id}"


def session_auto_approved(user: str, session_id: str | None) -> bool:
	if not user or not session_id:
		return False
	return bool(frappe.cache().get_value(_session_key(user, session_id)))


@frappe.whitelist(methods=["POST"])
def set_session_auto_approve(session_id: str, enabled: bool | int | str = True) -> dict:
	"""Let the caller's non-destructive writes in one conversation run without a card.

	Honoured only when the administrator allowed it in AI Assistant Settings, and
	never for deletes: those keep asking unless the site auto-approves everything.
	"""
	user = frappe.session.user
	if user == "Guest":
		frappe.throw(_("Authentication required"), frappe.AuthenticationError)
	session_id = _text(session_id)
	if not session_id:
		frappe.throw(_("A conversation is required."))
	if not session_auto_approve_allowed():
		frappe.throw(_("Allowing all changes in a conversation is turned off on this site."))
	key = _session_key(user, session_id)
	if _truthy(enabled):
		frappe.cache().set_value(key, 1, expires_in_sec=_SESSION_TTL_SECONDS)
	else:
		frappe.cache().delete_value(key)
	return {"ok": True, "enabled": _truthy(enabled)}


def _truthy(value) -> bool:
	if isinstance(value, str):
		return value.strip().lower() in ("1", "true", "yes", "on")
	return bool(value)


# Each user may pick how their own writes are approved, from the sidebar, within what the site
# allows: never looser than the site's mode, raised to auto-approving creates and updates only when
# the administrator lets users opt in. Read only and asking are always open to anyone.
_USER_MODE_KEY = "frappe_ai_write_approval"
_STRICTNESS = {MODE_READ_ONLY: 0, MODE_ASK: 1, MODE_AUTO_EDITS: 2, MODE_AUTO_ALL: 3}
_MENU_ORDER = (MODE_ASK, MODE_AUTO_EDITS, MODE_AUTO_ALL, MODE_READ_ONLY)


def allowed_user_modes() -> list[str]:
	"""The approval modes a user may choose on this site, in the order the sidebar lists them."""
	ceiling = site_mode()
	if ceiling == MODE_READ_ONLY:
		return [MODE_READ_ONLY]
	if session_auto_approve_allowed() and _STRICTNESS[ceiling] < _STRICTNESS[MODE_AUTO_EDITS]:
		ceiling = MODE_AUTO_EDITS
	return [m for m in _MENU_ORDER if _STRICTNESS[m] <= _STRICTNESS[ceiling]]


def user_mode(user: str) -> str:
	"""The user's own mode when they chose one the site still allows; the site's mode otherwise."""
	chosen = frappe.defaults.get_user_default(_USER_MODE_KEY, user) if user else None
	return chosen if chosen in allowed_user_modes() else site_mode()


@frappe.whitelist()
def get_approval_modes() -> dict:
	"""What the sidebar's approval menu shows: the caller's current mode and the ones they may pick."""
	user = frappe.session.user
	if user == "Guest":
		frappe.throw(_("Authentication required"), frappe.AuthenticationError)
	return {"current": user_mode(user), "allowed": allowed_user_modes(), "site": site_mode()}


@frappe.whitelist(methods=["POST"])
def set_approval_mode(mode: str) -> dict:
	"""Save the caller's approval mode; refused when the site does not allow it."""
	user = frappe.session.user
	if user == "Guest":
		frappe.throw(_("Authentication required"), frappe.AuthenticationError)
	mode = _text(mode)
	if mode not in allowed_user_modes():
		frappe.throw(_("That approval mode is not available on this site."))
	frappe.defaults.set_user_default(_USER_MODE_KEY, mode, user)
	return get_approval_modes()


def decide(
	tool: str,
	user: str,
	session_id: str | None,
	risk: str | None = None,
	doctype: str | None = None,
) -> str:
	"""``allow``, ``ask`` or ``deny`` for one write by this user in this conversation."""
	mode = user_mode(user)
	if mode == MODE_READ_ONLY:
		return "deny"
	if risk == "always-ask" or (doctype and doctype in ALWAYS_ASK_DOCTYPES):
		return "ask"
	if mode == MODE_AUTO_ALL:
		return "allow"
	if tool in DESTRUCTIVE_TOOLS:
		return "ask"
	if mode == MODE_AUTO_EDITS:
		return "allow"
	if session_auto_approve_allowed() and session_auto_approved(user, session_id):
		return "allow"
	return "ask"


# ──────────────────────────────────────────────────────────────────────────
# Tokens
# ──────────────────────────────────────────────────────────────────────────


def parse_confirmation(raw) -> dict | None:
	"""The sidebar's ``{tool, arguments}`` as a dict, or None when nothing was sent.

	frappe.whitelist hands nested arguments over as JSON strings; anything but a
	dict with a short tool name and a dict of arguments is refused.
	"""
	if raw is None or raw == "":
		return None
	if isinstance(raw, str):
		try:
			raw = json.loads(raw)
		except (json.JSONDecodeError, ValueError):
			frappe.throw(_("The confirmation could not be read."))
	if not isinstance(raw, dict):
		frappe.throw(_("The confirmation could not be read."))
	tool = _text(raw.get("tool"))
	if not tool or len(tool) > _TOOL_MAX_LEN:
		frappe.throw(_("The confirmation names no tool."))
	arguments = raw.get("arguments") or {}
	if not isinstance(arguments, dict):
		frappe.throw(_("The confirmation's arguments could not be read."))
	try:
		encoded = json.dumps(arguments)
	except (TypeError, ValueError):
		encoded = None
	if encoded is None:
		frappe.throw(_("The confirmation's arguments could not be read."))
	elif len(encoded.encode("utf-8")) > _ARGUMENTS_MAX_BYTES:
		frappe.throw(_("The confirmation's arguments are too large."))
	return {"tool": tool, "arguments": arguments}


# A batch's token stands for "this many rows of this doctype", as frappe-mcp-server redeems it
# (BatchConfirmationName): spent on a batch of another size, it is refused.
BATCH_TOOL = "create_documents"


def batch_name(arguments: dict) -> str:
	rows = arguments.get("rows")
	return f"{len(rows) if isinstance(rows, list) else 0} rows"


def issue(user: str, tool: str, arguments: dict) -> str:
	"""Mint the one-time token for this call. Not whitelisted: only the relay mints."""
	if not user or user == "Guest":
		raise frappe.AuthenticationError("a confirmation needs a user")
	token = secrets.token_urlsafe(32)
	record = {
		"user": user,
		"tool": tool,
		"doctype": _text(arguments.get("doctype")),
		"name": batch_name(arguments) if tool == BATCH_TOOL else _text(arguments.get("name")),
	}
	frappe.cache().set_value(_TOKEN_KEY_PREFIX + token, record, expires_in_sec=_TOKEN_TTL_SECONDS)
	return token


def _take(token: str) -> dict | None:
	"""The token's record, removed in the same step so a second redeem finds nothing."""
	cache = frappe.cache()
	key = cache.make_key(_TOKEN_KEY_PREFIX + token)
	try:
		raw = cache.getdel(key)
	except Exception:
		# an older Redis without GETDEL: the window between the two calls is the
		# only time a token could be spent twice, and a write needs the sid too
		raw = cache.get(key)
		if raw is not None:
			cache.delete(key)
	if raw is None:
		return None
	try:
		import pickle  # nosec B403 - frappe.cache stores its own values pickled

		record = pickle.loads(raw)  # nosec B301 - written by issue() above, never by a client
	except Exception:
		return None
	return record if isinstance(record, dict) else None


@frappe.whitelist(methods=["POST"])
def redeem(token: str, tool: str, doctype: str = "", name: str = "") -> dict:
	"""Spend a token for exactly the call it was minted for. Called by frappe-mcp-server.

	``{"ok": true}`` once per token, as the user it was minted for, for the same
	tool, doctype and name; ``{"ok": false}`` for everything else, with the reason
	in the site log rather than the response.
	"""
	user = frappe.session.user
	if user == "Guest":
		frappe.throw(_("Authentication required"), frappe.AuthenticationError)
	token = _text(token)
	tool = _text(tool)
	if not token or len(token) > 512 or not tool:
		return {"ok": False}
	record = _take(token)
	if record is None:
		_logger().warning(f"confirmation refused: unknown or spent token user={user} tool={tool}")
		return {"ok": False}
	expected = (record.get("user"), record.get("tool"), record.get("doctype"), record.get("name"))
	given = (user, tool, _text(doctype), _text(name))
	if expected != given:
		_logger().warning(
			"confirmation refused: token was minted for another call "
			f"user={user} tool={tool} doctype={given[2]!r} name={given[3]!r}"
		)
		return {"ok": False}
	_logger().info(f"confirmation redeemed user={user} tool={tool} doctype={given[2]!r} name={given[3]!r}")
	return {"ok": True}
