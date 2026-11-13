// Sales Invoice = the Laravel "Edit Invoice Status" screen.
//   Freight Pricing : CBM / KG Price x Exchange Rate -> item rate
//   Payments        : Create -> Record Payment (allocated!) and a live payments table
frappe.ui.form.on("Sales Invoice", {
	refresh(frm) {
		load_payments(frm);

		if (frm.doc.docstatus === 1) {
			// The ONLY correct way to take money: allocated against this invoice.
			if (flt(frm.doc.outstanding_amount) > 0) {
				frm.add_custom_button(__("Record Payment"), () => open_payment_entry(frm), __("Create"))
					.addClass("btn-primary");
			}
			frm.add_custom_button(__("Final Invoice"), () => print_doc(frm, "Final Invoice"), __("Print"));
			frm.add_custom_button(__("Cargo Receipt"), () => {
				if (!frm.doc.kf_cargo_load) {
					frappe.msgprint(__("This invoice is not linked to a cargo load."));
					return;
				}
				frappe.utils.print("Cargo Load", frm.doc.kf_cargo_load, "Cargo Receipt");
			}, __("Print"));
			frm.add_custom_button(__("Release Order"), () => print_release_order(frm), __("Print"));
			frm.add_custom_button(__("Refresh Payments"), () => load_payments(frm), __("Open"));
		}
		show_summary(frm);
	},

	kf_cbm_price(frm) { price_rows(frm); },
	kf_exchange_rate(frm) { price_rows(frm); },
});

// ------------------------------------------------------------------ payments
function open_payment_entry(frm) {
	// ERPNext's own mapper: pre-fills party, the invoice reference and the balance.
	frappe.call({
		method: "erpnext.accounts.doctype.payment_entry.payment_entry.get_payment_entry",
		args: { dt: frm.doc.doctype, dn: frm.doc.name },
		callback(r) {
			if (!r.message) {
				frappe.msgprint(__("Could not prepare a Payment Entry."));
				return;
			}
			const doc = frappe.model.sync(r.message)[0];
			frappe.set_route("Form", doc.doctype, doc.name);
			frappe.show_alert({
				message: __("Payment Entry prepared for {0}. Submit it to record the money.", [
					format_currency(frm.doc.outstanding_amount),
				]),
				indicator: "blue",
			}, 8);
		},
	});
}

function load_payments(frm) {
	if (frm.is_new() || !frm.doc.name) return;
	frappe.call({
		method: "kodafreight.api.invoice_payments.for_invoice",
		args: { invoice: frm.doc.name },
		callback(r) {
			const rows = r.message || [];
			frm.clear_table("kf_payments");
			rows.forEach((row) => {
				const child = frm.add_child("kf_payments");
				child.payment_date = row.payment_date;
				child.amount = row.amount;
				child.mode_of_payment = row.mode_of_payment;
				child.reference_no = row.reference_no;
				child.received_by = row.received_by;
				child.payment_entry = row.payment_entry;
				child.notes = row.notes;
			});
			frm.refresh_field("kf_payments");

			// Status mirror, straight from the numbers.
			if (frm.doc.docstatus === 0) {
				frm.set_value("kf_invoice_status", "Draft");
			} else if (!rows.length) {
				frm.set_value("kf_invoice_status", "Pending");
			} else if (flt(frm.doc.outstanding_amount) <= 0) {
				frm.set_value("kf_invoice_status", "Full Paid");
			} else {
				frm.set_value("kf_invoice_status", "Partial Paid");
			}

			if (frm.doc.docstatus === 1 && !rows.length && flt(frm.doc.outstanding_amount) > 0) {
				frm.dashboard.set_headline(
					__("No payment recorded yet. Use Create -> Record Payment; it must be linked to this invoice {0}.", [
						frm.doc.name,
					])
				);
			}
		},
	});
}

// ------------------------------------------------------------------ pricing
function price_rows(frm) {
	const price = flt(frm.doc.kf_cbm_price);       // CBM / KG Price
	const fx = flt(frm.doc.kf_exchange_rate) || 1;
	const rate = price * fx;
	const qty = (frm.doc.items || [])
		.filter((r) => r.item_code === "Freight Charges")
		.reduce((sum, r) => sum + flt(r.qty), 0);

	frm.set_value("kf_amount_usd", flt(price * qty, 2));
	frm.set_value("kf_computed_rate", rate);

	if (!price) {
		if (fx !== 1) {
			frm.dashboard.set_headline(
				__("Enter the CBM / KG Price first (e.g. 200) - the exchange rate {0} only converts it.", [fx])
			);
		}
		return;
	}

	(frm.doc.items || []).forEach((row) => {
		if (row.item_code !== "Freight Charges") return;
		frappe.model.set_value(row.doctype, row.name, "rate", rate);
	});
	show_summary(frm);
}

function show_summary(frm) {
	if (!frm.doc.kf_cbm_price) return;
	const fx = flt(frm.doc.kf_exchange_rate) || 1;
	const rate = flt(frm.doc.kf_cbm_price) * fx;
	const qty = (frm.doc.items || [])
		.filter((r) => r.item_code === "Freight Charges")
		.reduce((sum, r) => sum + flt(r.qty), 0);
	frm.dashboard.set_headline(
		__("Rate per unit {0} x {1} = {2} | Amount {3} | Paid {4} | Balance {5}", [
			frm.doc.kf_cbm_price, fx, format_currency(rate),
			format_currency(rate * qty),
			format_currency(flt(frm.doc.grand_total) - flt(frm.doc.outstanding_amount)),
			format_currency(frm.doc.outstanding_amount || 0),
		])
	);
}

// ------------------------------------------------------------------ printing
function print_doc(frm, format_name) {
	frappe.utils.print(
		frm.doc.doctype, frm.doc.name, format_name,
		frm.doc.letter_head || null, frm.doc.language || null
	);
}

function print_release_order(frm) {
	if (flt(frm.doc.outstanding_amount) > 0) {
		frappe.msgprint({
			title: __("Invoice not fully paid"),
			indicator: "red",
			message: __("{0} is still outstanding on {1}. A release order needs a fully paid invoice.", [
				format_currency(frm.doc.outstanding_amount), frm.doc.name,
			]),
		});
		return;
	}
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
			frappe.confirm(__("No release order exists for {0}. Create one now?", [frm.doc.name]), () => {
				frappe.new_doc("Release Order", {
					customer: frm.doc.customer,
					customer_name: frm.doc.customer_name,
					sales_invoice: frm.doc.name,
				});
			});
		},
	});
}
