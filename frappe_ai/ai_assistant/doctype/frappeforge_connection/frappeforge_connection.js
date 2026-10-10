// Copyright (c) 2026, Vyogo and contributors
// For license information, please see license.txt

frappe.ui.form.on("FrappeForge Connection", {
	refresh(frm) {
		if (frm.doc.status !== "Active") {
			frm.dashboard.set_headline_alert(__("This connection is {0}; its key no longer works.", [__(frm.doc.status)]), "red");
			return;
		}
		frm.add_custom_button(__("Rotate key"), () => window.frappe_ai_frappeforge.rotate(frm.doc.name));
		frm.add_custom_button(__("Revoke"), () =>
			frappe.confirm(__("Revoke this connection? FrappeForge loses access immediately."), () =>
				frappe
					.call({ method: "frappe_ai.api.frappeforge.revoke", args: { connection_name: frm.doc.name }, freeze: true })
					.then(() => frm.reload_doc())
			)
		);
	},
});
