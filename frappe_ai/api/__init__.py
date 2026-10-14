"""Public API for the AI Sidebar frontend."""

import frappe

from frappe_ai.api import confirm


@frappe.whitelist()
def get_settings() -> dict:
	"""Return AI Assistant Settings for the frontend sidebar."""
	settings = frappe.get_single("AI Assistant Settings")
	write_approval = getattr(settings, "write_approval", None)
	return {
		"enabled": bool(settings.enabled),
		"sidebar_width": getattr(settings, "sidebar_width", None) or 380,
		# Keep the fallback in sync with the doctype default — `Ctrl+/` is
		# reserved by Frappe v16 (toggle left sidebar) so we can't use it.
		"keyboard_shortcut": getattr(settings, "keyboard_shortcut", None) or "Alt+/",
		# How a write the assistant wants to make is approved; the card reads
		# these to know whether to offer "Allow all in this conversation".
		"write_approval": write_approval if write_approval in confirm.MODES else confirm.MODE_ASK,
		"allow_session_auto_approve": bool(getattr(settings, "allow_session_auto_approve", 0)),
	}
