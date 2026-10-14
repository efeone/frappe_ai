import frappe


def execute():
	"""Turn on "Let Users Turn On Auto-Approval" for sites that had the setting before it defaulted on."""
	frappe.db.set_single_value("AI Assistant Settings", "allow_session_auto_approve", 1)
