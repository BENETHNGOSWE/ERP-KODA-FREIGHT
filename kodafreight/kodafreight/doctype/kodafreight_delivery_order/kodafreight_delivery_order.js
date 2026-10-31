// Delivery Order form: pull the customer and release order from the invoice.
frappe.ui.form.on("Kodafreight Delivery Order", {
	sales_invoice(frm) {
		if (!frm.doc.sales_invoice) return;
		frappe.db.get_value(
			"Sales Invoice",
			frm.doc.sales_invoice,
			["customer", "customer_name", "outstanding_amount"],
			(r) => {
				const v = r.message || {};
				frm.set_value("customer", v.customer);
				frm.set_value("customer_name", v.customer_name);
				if (flt(v.outstanding_amount) > 0) {
					frappe.msgprint({
						title: __("Not fully paid"),
						indicator: "red",
						message: __("Invoice {0} is not fully paid. Cargo cannot be dispatched.", [
							frm.doc.sales_invoice,
						]),
					});
				}
			}
		);
		frappe.call({
			method: "frappe.client.get_list",
			args: {
				doctype: "Kodafreight Release Order",
				filters: { sales_invoice: frm.doc.sales_invoice, docstatus: 1 },
				fields: ["name"],
				limit_page_length: 1,
			},
			callback: (r) => {
				const rows = r.message || [];
				if (rows.length) frm.set_value("release_order", rows[0].name);
			},
		});
	},
});
