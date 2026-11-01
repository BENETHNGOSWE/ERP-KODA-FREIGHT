// Cargo Load form.
// Flow the operations team expects:
//   Load Details -> pick Unit Type -> the quantity field below is labelled with that unit
//   Measurements (L x W x H) are OPTIONAL: tick the box only when the carton was measured.
frappe.ui.form.on("Cargo Load", {
	refresh(frm) {
		apply_quantity_label(frm);

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

		if (frm.doc.docstatus === 1 && frm.doc.invoice_status !== "Paid") {
			frm.add_custom_button(__("Create / Update Invoice"), () => {
				frappe.confirm(__("Price this load and create or refresh its Sales Invoice?"), () => {
					frm.call("make_invoice").then((r) => {
						frm.reload_doc();
						if (r.message) {
							frappe.show_alert({ message: __("Invoice {0} ready", [r.message]), indicator: "green" });
						}
					});
				});
			}).addClass("btn-primary");
		}

		if (frm.doc.container) {
			frm.add_custom_button(__("Container"), () => {
				frappe.set_route("Form", "Container", frm.doc.container);
			}, __("Open"));
		}

		if (frm.doc.docstatus === 1 && frm.doc.invoice_status === "Paid") {
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

	// Pick the unit first, then the value field underneath tells you what to type.
	unit_type(frm) {
		apply_quantity_label(frm);
		recalc(frm);
	},
	measure_dimensions(frm) { recalc(frm); },
	length(frm) { recalc(frm); },
	width(frm) { recalc(frm); },
	height(frm) { recalc(frm); },
	quantity(frm) { recalc(frm); },
	rate(frm) { recalc(frm); },
	exchange_rate(frm) { recalc(frm); },
	transport_fee(frm) { recalc(frm); },
	ocean_mode(frm) {
		if (!frm.doc.ocean_mode && frm.doc.container) {
			frappe.msgprint(__("Air freight cannot stay in a container. Clear the Container field."));
		}
	},
});

function apply_quantity_label(frm) {
	const unit = frm.doc.unit_type;
	if (!unit) {
		frm.set_df_property("quantity", "label", __("Quantity"));
		frm.set_df_property("quantity", "description", __("Select a Unit Type first (CBM, KG, pieces ...)."));
		return;
	}
	frappe.db.get_value("Unit Type", unit, ["symbol", "unit_name", "uom"], (r) => {
		const v = r.message || {};
		frm._unit_symbol = [v.symbol, v.uom, v.unit_name].join(" ");
		const label = v.symbol ? __("Quantity ({0})", [v.symbol]) : __("Quantity ({0})", [v.unit_name || unit]);
		frm.set_df_property("quantity", "label", label);
		frm.set_df_property(
			"quantity",
			"description",
			__("Type the billable quantity in {0}. You do not have to measure the box.", [v.unit_name || unit])
		);
	});
}

function recalc(frm) {
	const d = frm.doc;
	const measured = (d.length || 0) * (d.width || 0) * (d.height || 0);
	const volume_unit = /m3|cbm|m³|cubic|cube|volume/i.test(
		[d.unit_type, frm._unit_symbol || ""].join(" ")
	);
	// Mirrors services/freight.py::billable_volume / billable_quantity
	const cbm = d.measure_dimensions && measured > 0 ? measured : volume_unit ? d.quantity || 0 : 0;
	const billable_qty = volume_unit ? cbm : d.quantity || 0;
	const fx = d.exchange_rate || 1;
	const freight = billable_qty * (d.rate || 0) * fx;

	frm.set_value("volume_cbm", flt(measured, 4));
	frm.set_value("billable_volume", flt(cbm, 4));
	frm.set_value("billable_quantity", flt(billable_qty, 4));
	frm.set_value("freight_amount", flt(freight, 2));
	frm.set_value("total_amount", flt(freight + (d.transport_fee || 0), 2));
}
