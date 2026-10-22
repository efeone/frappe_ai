import frappe


def execute():
	"""Move sites still on the old stored default name ("AI Sidebar") to "Vyogo AI".

	A Single's field default is only written when the form is first saved, so a site that
	saved the settings while "AI Sidebar" was the default keeps it. A name someone typed
	themselves is left alone.
	"""
	current = frappe.db.get_single_value("AI Assistant Settings", "assistant_name")
	if current == "AI Sidebar":
		frappe.db.set_single_value("AI Assistant Settings", "assistant_name", "Vyogo AI")
