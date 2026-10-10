# Copyright (c) 2026, Vyogo and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class FrappeForgeChangeLog(Document):
	def on_trash(self):
		frappe.throw(frappe._("The FrappeForge change log cannot be deleted."))
