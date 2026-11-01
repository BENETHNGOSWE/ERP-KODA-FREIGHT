// Sales Invoice = the Laravel "edit invoice" screen.
// Pricing panel + the two documents the cashier used to print from it:
//   Final Invoice  -> send to the client, shows exactly what is still owed
//   Release Order  -> only for a FULLY PAID invoice
frappe.ui.form.on("Sales Invoice", {
	refresh(frm) {
		if (frm.doc.docstatus === 1) {
			if (frm.doc.outstanding_amount > 0) {
				frm.add_custom_button(__("Record Payment"), () => {
					frappe.new_doc("Payment Entry", {
						payment_type: "Receive",
						party_type: "Customer",
						party: frm.doc.customer,
						paid_amount: frm.doc.outstanding_amount,
						received_amount: frm.doc.outstanding_amount,
					});
				}, __("Create")).addClass("btn-primary");
			}

			frm.add_custom_button(__("Final Invoice"), () => {
				print_doc(frm, "Final Invoice");
			}, __("Print"));

			frm.add_custom_button(__("Cargo Receipt"), () => {
				if (!frm.doc.kf_cargo_load) {
					frappe.msgprint(__("This invoice is not linked to a cargo load."));
					return;
				}
				frappe.utils.print("Cargo Load", frm.doc.kf_cargo_load, "Cargo Receipt");
			}, __("Print"));

			frm.add_custom_button(__("Release Order"), () => {
				if (flt(frm.doc.outstanding_amount) > 0) {
					frappe.msgprint({
						title: __("Invoice not fully paid"),
						indicator: "red",
						message: __(
							"{0} is still outstanding on {1}. A release order can only be printed for a fully paid invoice.",
							[format_currency(frm.doc.outstanding_amount), frm.doc.name]
						),
					});
					return;
				}
				print_release_order(frm);
			}, __("Print"));
		}
		show_summary(frm);
	},

	kf_cbm_price(frm) { price_rows(frm); },
	kf_exchange_rate(frm) { price_rows(frm); },
});

function print_doc(frm, format_name) {
	frappe.utils.print(
		frm.doc.doctype, frm.doc.name, format_name,
		frm.doc.letter_head || null, frm.doc.language || null
	);
}

function print_release_order(frm) {
	frappe.call({
		method: "frappe.client.get_list",
		args: {
			doctype: "Release Order",
			filters: { sales_invoice: frm.doc.name, docstatus: 1 },
			fields: ["name"],
			limit_page_length: 1,
		},
		callback(r) {
			const rows = r.message || [];
			if (rows.length) {
				frappe.utils.print("Release Order", rows[0].name, "Release Order");
				return;
			}
			// none yet -> offer to create it (server enforces the payment rule too)
			frappe.confirm(
				__("No release order exists for {0}. Create one now?", [frm.doc.name]),
				() => {
					frappe.new_doc("Release Order", {
						client: frm.doc.kf_client || undefined,
						customer: frm.doc.customer,
						customer_name: frm.doc.customer_name,
						sales_invoice: frm.doc.name,
						customer_phone: frm.doc.contact_mobile,
					});
				}
			);
		},
	});
}

function price_rows(frm) {
	const price = flt(frm.doc.kf_cbm_price);
	const fx = flt(frm.doc.kf_exchange_rate) || 1;
	if (!price) return;
	(frm.doc.items || []).forEach((row) => {
		if (row.item_code !== "Freight Charges") return;
		frappe.model.set_value(row.doctype, row.name, "rate", price * fx);
	});
	show_summary(frm);
}

function show_summary(frm) {
	if (!frm.doc.kf_cbm_price) return;
	frm.dashboard.set_headline(
		__("Freight: {0} x {1} = {2} per unit | Balance: {3}", [
			format_currency(frm.doc.kf_cbm_price),
			flt(frm.doc.kf_exchange_rate) || 1,
			format_currency(flt(frm.doc.kf_cbm_price) * (flt(frm.doc.kf_exchange_rate) || 1)),
			format_currency(frm.doc.outstanding_amount || 0),
		])
	);
}
