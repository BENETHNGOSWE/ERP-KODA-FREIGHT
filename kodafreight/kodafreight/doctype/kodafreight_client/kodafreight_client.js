// Copyright (c) 2026, Kodafreight and contributors

frappe.ui.form.on("Kodafreight Client", {
	refresh(frm) {
		if (frm.is_new()) return;

		if (frappe.user.has_role("Kodafreight Platform Admin")) {
			frm.add_custom_button(__("Allocate Credits"), () => {
				frappe.prompt(
					[
						{ fieldname: "quantity", label: __("Quantity"), fieldtype: "Int", reqd: 1 },
						{ fieldname: "remarks", label: __("Remarks"), fieldtype: "Small Text" },
					],
					(values) => {
						frm.call("allocate_credits", values).then((r) => {
							if (r.message) {
								frappe.show_alert({
									message: __("New balance: {0}", [r.message.balance]),
									indicator: "green",
								});
								frm.reload_doc();
							}
						});
					},
					__("Allocate SMS Credits"),
					__("Allocate")
				);
			}, __("Credits"));

			frm.add_custom_button(__("Credit Ledger"), () => {
				frappe.set_route("List", "Kodafreight SMS Credit Transaction", { client: frm.doc.name });
			}, __("Credits"));

			frm.add_custom_button(__("SMS History"), () => {
				frappe.set_route("List", "Kodafreight SMS Message", { client: frm.doc.name });
			});

			const label = frm.doc.status === "Active" ? __("Deactivate") : __("Activate");
			frm.add_custom_button(label, () => {
				frm.set_value("status", frm.doc.status === "Active" ? "Inactive" : "Active");
				frm.save();
			});
		}

		if (frm.doc.credit_balance <= (frm.doc.low_balance_threshold || 0)) {
			frm.dashboard.add_indicator(__("Low SMS balance: {0}", [frm.doc.credit_balance || 0]), "red");
		} else {
			frm.dashboard.add_indicator(__("SMS balance: {0}", [frm.doc.credit_balance || 0]), "green");
		}
	},
});
