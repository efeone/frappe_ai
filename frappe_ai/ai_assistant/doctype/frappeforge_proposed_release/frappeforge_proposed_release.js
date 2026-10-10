// Copyright (c) 2026, Vyogo and contributors
// For license information, please see license.txt

// The change view is rendered and escaped on the server (frappe_ai.frappeforge.deploy.render_changes_html)
// from the tarball itself; the agent's summary is shown separately and labelled as the agent's.
frappe.ui.form.on("FrappeForge Proposed Release", {
	refresh(frm) {
		frm.get_field("changes_html").$wrapper.html(frm.doc.__onload?.changes_html || "");
		const status = frm.doc.status;
		if (status === "Applying" || status === "Approved") {
			frm.dashboard.set_headline_alert(__("Applying this release…"), "blue");
			setTimeout(() => frm.reload_doc(), 3000);
		}
		if (status !== "Pending" || !frappe.user.has_role("System Manager")) return;
		if (frm.doc.has_executable) {
			frm.dashboard.set_headline_alert(__("This release contains code that will run on this site. Read every script before approving."), "orange");
		}
		frm.add_custom_button(__("Approve and apply"), () =>
			frappe.confirm(__("Apply release {0} {1} to this site now?", [frm.doc.package, frm.doc.version]), () =>
				frappe
					.call({ method: "frappe_ai.api.frappeforge.approve", args: { release: frm.doc.name }, freeze: true })
					.then(() => frm.reload_doc())
			)
		).addClass("btn-primary");
		frm.add_custom_button(__("Reject"), () =>
			frappe.prompt({ fieldtype: "Small Text", fieldname: "reason", label: __("Reason (FrappeForge will see it)") }, (v) =>
				frappe
					.call({ method: "frappe_ai.api.frappeforge.reject", args: { release: frm.doc.name, reason: v.reason }, freeze: true })
					.then(() => frm.reload_doc())
			)
		);
	},
});
