# Copyright (c) 2026, Vyogo and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class VyogoForgeAIProposedRelease(Document):
	def onload(self):
		from frappe_ai.frappeforge import deploy

		self.set_onload("changes_html", deploy.render_changes_html(self))
