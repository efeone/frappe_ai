# Copyright (c) 2026, Vyogo and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_to_date, get_datetime, now_datetime

from frappe_ai.frappeforge import connection


class FrappeForgeConnection(Document):
	def validate(self):
		self.allowed_ips = ",".join(connection.parse_ips(self.allowed_ips))
		self.read_roles = "\n".join(connection.parse_roles(self.read_roles))
		limit = add_to_date(now_datetime(), days=connection.MAX_EXPIRY_DAYS)
		if self.expires_on and get_datetime(self.expires_on) > limit:
			frappe.throw(
				_("A connection can be valid for at most {0} days from now.").format(
					connection.MAX_EXPIRY_DAYS
				)
			)

	def on_update(self):
		if self.user and self.status == "Active":
			self.sync_user()

	def sync_user(self):
		"""Keep the agent user's roles and IP restriction equal to this connection's."""
		user = frappe.get_doc("User", self.user)
		wanted = {connection.AGENT_ROLE, *connection.parse_roles(self.read_roles)}
		user.set("roles", [r for r in user.roles if r.role in wanted])
		for role in wanted - {r.role for r in user.roles}:
			user.append("roles", {"role": role})
		user.restrict_ip = self.allowed_ips or None
		user.save(ignore_permissions=True)
		connection.sync_token_expiry(self.user, self.expires_on)
		connection._forget(self.user)

	def on_trash(self):
		frappe.throw(frappe._("Revoke a FrappeForge connection instead of deleting it; its history stays."))
