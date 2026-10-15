# Copyright (c) 2026, Vyogo and contributors
# For license information, please see license.txt

"""Writes from MCP clients other than the sidebar's agent.

frappe-mcp-server is the site's one gateway for every AI client. Who may change data
through it depends on how the caller signed in:

- the sidebar's agent acts on the user's browser session (the ``sid`` cookie). The model
  chooses its writes, so each needs the one-time token of ``confirm`` that only the
  user's Allow (or their approval mode) mints. Nothing here ever lets a session write.
- other clients (Claude Desktop, Claude Code, a script) sign in as a user with an OAuth
  token or an API key, and their own app asks the person before each call. Without a
  token, frappe-mcp-server asks ``authorize``: the site's "Writes from Other MCP Clients"
  setting answers, within what the user may do, and every answer is logged.
- Vyogo Forge AI's agent user is told to propose: frappe-mcp-server then files the write
  as a proposed release (``frappe_ai.api.frappeforge.propose_change``), which a System
  Manager approves, and which is logged and can be rolled back.
"""

from __future__ import annotations

import frappe
from frappe import _

from frappe_ai.api import confirm
from frappe_ai.frappeforge import connection

MODE_READ_ONLY = "Read only"
MODE_EDITS = "Allow creates and updates"
MODE_ALL = "Allow all changes"

CALLER_SESSION = "session"
CALLER_OAUTH = "oauth"
CALLER_API_KEY = "api_key"

# the write tools of frappe-mcp-server and the permission each needs
WRITE_TOOLS = {
	"create_document": "create",
	"create_documents": "create",
	"update_document": "write",
	"delete_document": "delete",
}
DESTRUCTIVE_TOOLS = confirm.DESTRUCTIVE_TOOLS

# Records that carry code or privilege, or that govern the assistant itself: an outside client
# never changes them, whatever the setting says. A person does it in Desk.
DESK_ONLY_DOCTYPES = confirm.ALWAYS_ASK_DOCTYPES | frozenset(
	{
		"Server Script",
		"Webhook",
		"Scheduled Job Type",
		"Custom HTML Block",
		"Website Theme",
		"Website Script",
		"Role",
		"Has Role",
		"DocPerm",
		"Workflow",
		"DocType",
		"Module Def",
		"System Settings",
		"OAuth Client",
		"OAuth Bearer Token",
		"OAuth Authorization Code",
		"AI Assistant Settings",
		"AI Skill",
		"AI External Write Log",
	}
)

LOG = "AI External Write Log"

PROPOSE = "Vyogo Forge AI's changes are proposed for a System Manager's approval."


def site_mode() -> str:
	mode = frappe.db.get_single_value("AI Assistant Settings", "external_mcp_writes")
	return mode if mode in (MODE_READ_ONLY, MODE_EDITS, MODE_ALL) else MODE_READ_ONLY


def caller() -> tuple[str, str]:
	"""How this request signed in, and the client it came through: (kind, client)."""
	request = getattr(frappe.local, "request", None)
	if request is None:
		return CALLER_SESSION, ""
	sid = request.cookies.get("sid")
	if sid and sid != "Guest":
		return CALLER_SESSION, ""
	scheme, _sep, credential = (request.headers.get("Authorization") or "").strip().partition(" ")
	scheme = scheme.lower()
	if scheme == "bearer" and credential.strip():
		return CALLER_OAUTH, _oauth_client(credential.strip())
	if scheme in ("token", "basic") and credential.strip():
		return CALLER_API_KEY, "API key"
	return CALLER_SESSION, ""


def _oauth_client(access_token: str) -> str:
	client = frappe.db.get_value("OAuth Bearer Token", {"access_token": access_token}, "client")
	app_name = frappe.db.get_value("OAuth Client", client, "app_name") if client else None
	return app_name or "OAuth"


def decide(
	user: str, kind: str, tool: str, doctype: str, name: str = "", client: str = ""
) -> tuple[bool, str]:
	"""Whether this outside write may go ahead, and why not. Every answer is logged."""
	ok, reason = _decide(user, kind, tool, doctype)
	outcome = "Proposed" if reason == PROPOSE else ("Allowed" if ok else "Refused")
	_log(user, client, tool, doctype, name, outcome, reason)
	return ok, reason


def _decide(user: str, kind: str, tool: str, doctype: str) -> tuple[bool, str]:
	if kind == CALLER_SESSION:
		return False, "A browser session writes only with the user's approval in the sidebar."
	if connection.is_agent_user(user):
		return False, PROPOSE
	if tool not in WRITE_TOOLS:
		return False, f"{tool or 'This tool'} is not a write this site allows from other MCP clients."
	if not doctype:
		return False, "The change names no DocType."
	mode = site_mode()
	if mode == MODE_READ_ONLY:
		return False, "This site is read only for other MCP clients (AI Assistant Settings)."
	if doctype in DESK_ONLY_DOCTYPES:
		return False, f"{doctype} records are changed in Desk only, not from an MCP client."
	if tool in DESTRUCTIVE_TOOLS and mode != MODE_ALL:
		return False, "This site does not allow other MCP clients to delete records."
	ptype = WRITE_TOOLS[tool]
	# the DocType-level right; Frappe checks the record itself when the write is made
	if not frappe.has_permission(doctype, ptype, user=user):
		return False, f"{user} has no permission to {ptype} {doctype}."
	return True, ""


def _log(user: str, client: str, tool: str, doctype: str, name: str, outcome: str, reason: str) -> None:
	frappe.get_doc(
		{
			"doctype": LOG,
			"user": user,
			"client": client[:140],
			"tool": tool[:140],
			"target_doctype": doctype[:140],
			"target_name": name[:140],
			"outcome": outcome,
			"reason": reason,
		}
	).insert(ignore_permissions=True)


@frappe.whitelist(methods=["POST"])
def authorize(tool: str, doctype: str = "", name: str = "") -> dict:
	"""Whether the caller may make this write without a sidebar token. Called by frappe-mcp-server.

	``{"ok": true, "reason": ""}`` or ``{"ok": false, "reason": "<a sentence for the person>"}``;
	``"propose": true`` when the write is to be filed as a proposal instead.
	"""
	user = frappe.session.user
	if user == "Guest":
		frappe.throw(_("Authentication required"), frappe.AuthenticationError)
	kind, client = caller()
	ok, reason = decide(user, kind, _text(tool), _text(doctype), _text(name), client)
	return {"ok": ok, "reason": reason, "propose": reason == PROPOSE}


def _text(value) -> str:
	return value.strip()[:200] if isinstance(value, str) else ""
