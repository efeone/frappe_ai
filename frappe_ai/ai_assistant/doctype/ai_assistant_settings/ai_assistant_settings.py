# Copyright (c) 2026, Vyogo and contributors
# For license information, please see license.txt

import re
from urllib.parse import urlparse

import frappe
from frappe.model.document import Document

from frappe_ai.api import confirm


class AIAssistantSettings(Document):
	def onload(self):
		# Populate the read-only agent_url display from site_config on every
		# form load. Without this, the field is empty on first open and
		# Frappe applies `hide-control` to empty read-only fields — the
		# admin can never see what URL the agent is wired to until they
		# click Save once. Refreshing on load keeps display + truth aligned.
		self._sync_agent_url_from_conf()

	def before_save(self):
		# Same sync runs on save so a manual edit (or an out-of-band
		# site_config change between load and save) can't desync the
		# displayed value from the authoritative one.
		self._sync_agent_url_from_conf()

	def _sync_agent_url_from_conf(self) -> None:
		conf_url = frappe.local.conf.get("frappe_ai_agent_url", "")
		self.agent_url = conf_url.rstrip("/") if conf_url else ""

	def validate(self):
		if self.timeout is not None and (self.timeout < 1 or self.timeout > 300):
			frappe.throw(frappe._("Timeout must be between 1 and 300 seconds."))
		if self.sidebar_width is not None and (self.sidebar_width < 300 or self.sidebar_width > 600):
			frappe.throw(frappe._("Sidebar width must be between 300 and 600 pixels."))
		if self.keyboard_shortcut:
			_validate_shortcut(self.keyboard_shortcut)
		if self.write_approval and self.write_approval not in confirm.MODES:
			frappe.throw(frappe._("Write Approval must be one of: {0}.").format(", ".join(confirm.MODES)))
		if self.mcp_url:
			self.mcp_url = self.mcp_url.strip().rstrip("/")
			parsed = urlparse(self.mcp_url)
			if parsed.scheme not in ("http", "https") or not parsed.netloc:
				frappe.throw(frappe._("MCP Server URL must start with https:// (or http://)."))
		self._validate_forge_settings()

	def _validate_forge_settings(self):
		from frappe_ai.frappeforge import connection

		if self.forge_allowed_ips:
			self.forge_allowed_ips = ",".join(connection.parse_ips(self.forge_allowed_ips))
		if self.forge_read_roles:
			self.forge_read_roles = "\n".join(connection.parse_roles(self.forge_read_roles))

	def on_update(self):
		if self.forge_user and self.forge_status == "Active":
			self.sync_forge_user()

	def sync_forge_user(self):
		from frappe_ai.frappeforge import connection

		if not self.forge_user or not frappe.db.exists("User", self.forge_user):
			return
		user = frappe.get_doc("User", self.forge_user)
		wanted = {connection.AGENT_ROLE, *connection.parse_roles(self.forge_read_roles)}
		user.set("roles", [r for r in user.roles if r.role in wanted])
		for role in wanted - {r.role for r in user.roles}:
			user.append("roles", {"role": role})
		user.restrict_ip = self.forge_allowed_ips or None
		user.save(ignore_permissions=True)
		if self.forge_expires_on:
			connection.sync_token_expiry(self.forge_user, self.forge_expires_on)
		connection._forget(self.forge_user)


_RESERVED_SHORTCUTS = {
	# Frappe v16 hard-binds these in the desk; binding the AI toggle to any of
	# them swallows the keystroke and toggles the wrong thing instead.
	"ctrl+/",  # Toggle left workspace sidebar
	"ctrl+k",  # Toggle Awesomebar
	"ctrl+g",  # Open Awesomebar
	"ctrl+s",  # Trigger Primary Action
	"alt+s",  # Open Settings
	"shift+/",  # Show Keyboard Shortcuts
}


def _validate_shortcut(shortcut: str) -> None:
	"""Accept modifier+key combos: e.g. Alt+/, Mod+Shift+A, Alt+;

	Rejects shortcuts already claimed by Frappe v16 (Ctrl+/, Ctrl+K, etc.) —
	the OS still delivers the keystroke, but Frappe's handler runs first and
	the AI toggle never fires.
	"""
	pattern = r"^(Mod|Ctrl|Alt|Shift)(\+(Mod|Ctrl|Alt|Shift))*\+\S$"
	if not re.match(pattern, shortcut, re.IGNORECASE):
		frappe.throw(frappe._("Invalid keyboard shortcut format. Use e.g. Alt+/ or Mod+Shift+A."))
	if shortcut.lower() in _RESERVED_SHORTCUTS:
		frappe.throw(
			frappe._("{0} is reserved by Frappe; pick a different combo (e.g. Alt+/, Mod+Shift+A).").format(
				shortcut
			)
		)
