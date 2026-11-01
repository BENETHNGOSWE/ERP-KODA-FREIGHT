// Cargo Load form: live CBM maths, one-button invoicing, and honest status display.
frappe.ui.form.on("Cargo Load", {
	refresh(frm) {
		if (frm.is_new()) return;

		if (frm.doc.sales_invoice) {
			frm.add_custom_button(__("Sales Invoice"), () => {
				frappe.set_route("Form", "Sales Invoice", frm.doc.sales_invoice);
			}, __("Open"));
			frm.add_custom_button(__("Refresh Payment Status"), () => {
				frm.call("refresh_invoice_status").then((r) => {
					frm.reload_doc();
					frappe.show_alert({ message: __("Status: {0}", [r.message]), indicator: "blue" });
				});
			}, __("Open"));
		}

		if (frm.doc.docstatus === 1 && !["Paid"].includes(frm.doc.invoice_status)) {
			frm.add_custom_button(__("Create / Update Invoice"), () => {
				frappe.confirm(
					__("Price this load and create or refresh its Sales Invoice?"),
					() => {
						frm.call("make_invoice").then((r) => {
							frm.reload_doc();
							if (r.message) {
								frappe.show_alert({
									message: __("Invoice {0} ready", [r.message]),
									indicator: "green",
								});
							}
						});
					}
				);
			}).addClass("btn-primary");
		}

		if (frm.doc.container) {
			frm.add_custom_button(__("Container"), () => {
				frappe.set_route("Form", "Container", frm.doc.container);
			}, __("Open"));
		}

		if (frm.doc.docstatus === 1 && frm.doc.invoice_status === "Paid" && !frm.doc.__islocal) {
			frm.add_custom_button(__("Release Order"), () => {
				frappe.new_doc("Release Order", {
					customer: frm.doc.customer,
					customer_name: frm.doc.customer_name,
					sales_invoice: frm.doc.sales_invoice,
					client: frm.doc.client,
				});
			}, __("Create"));
		}
	},

	use_manual_volume(frm) { recalc(frm); },
	manual_volume(frm) { recalc(frm); },
	length(frm) { recalc(frm); },
	width(frm) { recalc(frm); },
	height(frm) { recalc(frm); },
	cbm_rate(frm) { recalc(frm); },
	exchange_rate(frm) { recalc(frm); },
	transport_fee(frm) { recalc(frm); },
});

function recalc(frm) {
	const d = frm.doc;
	const measured = (d.length || 0) * (d.width || 0) * (d.height || 0);
	const billable = d.use_manual_volume ? d.manual_volume || 0 : measured;
	const fx = d.exchange_rate || 1;
	const freight = billable * (d.cbm_rate || 0) * fx;
	const total = freight + (d.transport_fee || 0);

	frm.set_value("volume_cbm", flt(measured, 4));
	frm.set_value("billable_volume", flt(billable, 4));
	frm.set_value("freight_amount", flt(freight, 2));
	frm.set_value("total_amount", flt(total, 2));
}
