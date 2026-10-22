"""Public API for the Vyogo AI frontend."""

import frappe


@frappe.whitelist()
def get_settings() -> dict:
	"""Return AI Assistant Settings for the frontend sidebar."""
	# Imported here, not at the top: a package that imports its own modules while
	# it loads deadlocks a threaded worker whose first requests arrive together
	# (one thread holds this package and waits for `confirm`, another the reverse).
	from frappe_ai.api import availability, branding, confirm

	settings = frappe.get_single("AI Assistant Settings")
	write_approval = getattr(settings, "write_approval", None)
	return {
		"enabled": availability.is_enabled(settings),
		# Turned off by the host (site_config), so the form cannot turn it back on.
		"enabled_managed": availability.switched_off_by_host(),
		"sidebar_width": getattr(settings, "sidebar_width", None) or 380,
		# Keep the fallback in sync with the doctype default — `Ctrl+/` is
		# reserved by Frappe v16 (toggle left sidebar) so we can't use it.
		"keyboard_shortcut": getattr(settings, "keyboard_shortcut", None) or "Alt+/",
		# How a write the assistant wants to make is approved; the card reads
		# these to know whether to offer "Allow all in this conversation".
		"write_approval": write_approval if write_approval in confirm.MODES else confirm.MODE_ASK,
		"allow_session_auto_approve": bool(getattr(settings, "allow_session_auto_approve", 0)),
		# Name, byline, logo and accent: the host's site_config first, then the form.
		**branding.resolve(settings),
	}
