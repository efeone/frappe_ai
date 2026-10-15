# Copyright (c) 2026, Vyogo and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class AIExternalWriteLog(Document):
	def on_trash(self):
		frappe.throw(frappe._("The AI external write log cannot be deleted."))
