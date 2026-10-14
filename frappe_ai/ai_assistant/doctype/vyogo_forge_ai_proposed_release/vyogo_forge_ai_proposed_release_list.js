// Copyright (c) 2026, Vyogo and contributors
// For license information, please see license.txt

frappe.listview_settings["Vyogo Forge AI Proposed Release"] = {
	get_indicator(doc) {
		const colour = { Pending: "orange", Approved: "blue", Applying: "blue", Applied: "green", Failed: "red", Rejected: "gray", Expired: "gray" };
		return [__(doc.status), colour[doc.status] || "gray", `status,=,${doc.status}`];
	},
};
