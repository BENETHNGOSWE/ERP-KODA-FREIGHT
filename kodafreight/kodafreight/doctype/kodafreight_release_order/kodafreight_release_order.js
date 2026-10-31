// Release Order form: warn early when the invoice is not paid, so the clerk is not
// surprised by a server-side refusal at save time.
frappe.ui.form.on("Kodafreight Release Order", {
	sales_invoice(frm) {
		if (!frm.doc.sales_invoice) return;
		frappe.db.get_value(
			"Sales Invoice",
			frm.doc.sales_invoice,
			["customer", "customer_name", "grand_total", "outstanding_amount", "docstatus"],
			(r) => {
				const v = r.message || {};
				frm.set_value("customer", v.customer);
				frm.set_value("customer_name", v.customer_name);
				frm.set_value("invoice_grand_total", v.grand_total);
				frm.set_value("invoice_outstanding", v.outstanding_amount);
				if (flt(v.outstanding_amount) > 0) {
					frappe.msgprint({
						title: __("Not fully paid"),
						indicator: "red",
						message: __(
							"Invoice {0} still has {1} outstanding. The release will be refused until it is fully paid.",
							[frm.doc.sales_invoice, format_currency(v.outstanding_amount)]
						),
					});
				}
			}
		);
	},
});
