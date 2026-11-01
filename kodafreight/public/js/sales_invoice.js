// Sales Invoice = the Laravel "edit invoice" screen.
// Adds the freight pricing panel behaviour and the actions a cashier expects.
frappe.ui.form.on("Sales Invoice", {
	refresh(frm) {
		if (frm.doc.docstatus === 1) {
			// "Record Payment" -> a real ERPNext Payment Entry
			if (frm.doc.outstanding_amount > 0) {
				frm.add_custom_button(__("Record Payment"), () => {
					frappe.new_doc("Payment Entry", {
						payment_type: "Receive",
						party_type: "Customer",
						party: frm.doc.customer,
						paid_amount: frm.doc.outstanding_amount,
						received_amount: frm.doc.outstanding_amount,
						reference_no: frm.doc.name,
						reference_date: frappe.datetime.get_today(),
					});
				}, __("Create")).addClass("btn-primary");
			}
			frm.add_custom_button(__("Final Invoice (PDF)"), () => {
				frappe.utils.print(
					frm.doc.doctype, frm.doc.name, "Final Invoice",
					frm.doc.letter_head, frm.doc.language
				);
			}, __("Print"));
		}
		show_summary(frm);
	},

	kf_cbm_price(frm) { price_rows(frm); },
	kf_exchange_rate(frm) { price_rows(frm); },
});

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
		__("Freight: {0} x {1} = {2} per unit", [
			format_currency(frm.doc.kf_cbm_price),
			flt(frm.doc.kf_exchange_rate) || 1,
			format_currency(flt(frm.doc.kf_cbm_price) * (flt(frm.doc.kf_exchange_rate) || 1)),
		])
	);
}
