// Copyright (c) 2026, Vyogo and contributors
// For license information, please see license.txt

frappe.ui.form.on("Vyogo Forge AI Change Log", {
	refresh(frm) {
		if (frm.doc.rollback_requested && frm.doc.status === "Applied") {
			frm.dashboard.set_headline_alert(__("Vyogo Forge AI asked for this change to be rolled back."), "orange");
		}
		if (frm.doc.status !== "Applied" || frm.doc.action === "Rollback" || !frappe.user.has_role("System Manager")) return;
		const what = frm.doc.action === "Create" ? __("delete {0}", [frm.doc.target_name]) : __("restore the previous version of {0}", [frm.doc.target_name]);
		frm.add_custom_button(__("Roll back"), () =>
			frappe.confirm(__("Roll back: {0}?", [what]), () =>
				frappe
					.call({ method: "frappe_ai.api.frappeforge.rollback", args: { change: frm.doc.name }, freeze: true })
					.then(() => frm.reload_doc())
			)
		);
	},
});
