// Copyright (c) 2026, Vyogo and contributors
// For license information, please see license.txt

frappe.listview_settings["FrappeForge Connection"] = {
	onload(listview) {
		listview.page.set_primary_action(__("Connect FrappeForge"), () => window.frappe_ai_frappeforge.connect());
	},
	get_indicator(doc) {
		return [__(doc.status), { Active: "green", Revoked: "red", Expired: "gray" }[doc.status] || "gray", `status,=,${doc.status}`];
	},
};
