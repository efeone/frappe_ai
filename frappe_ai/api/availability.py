"""Whether the assistant is on for this site.

AI Assistant Settings > Enabled is the site's own switch. A host that runs the
site can also turn the assistant off from outside, in site_config:

	frappe_ai_enabled   0 / false turns it off; the form cannot turn it back on

A host's "on" never overrides the site's own "off". While the host has it off
the sidebar is not shown, administrators get no hint to enable it (the form
could not), and chat is refused. Nothing is uninstalled, so conversations are
kept for when it is turned back on.
"""

import frappe

HOST_KEY = "frappe_ai_enabled"


def switched_off_by_host() -> bool:
	value = frappe.local.conf.get(HOST_KEY)
	if value is None or value == "":
		return False
	if isinstance(value, str):
		return value.strip().lower() in ("0", "false", "no", "off")
	return not value


def is_enabled(settings) -> bool:
	return bool(settings.enabled) and not switched_off_by_host()
