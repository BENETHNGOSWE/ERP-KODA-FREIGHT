// Container form: manifest clarity and the two shipping actions.
frappe.ui.form.on("Container", {
	refresh(frm) {
		if (frm.doc.docstatus === 1 && frm.doc.status !== "Arrived") {
			frm.add_custom_button(__("Mark Shipped"), () => {
				frm.call("mark_shipped").then(() => frm.reload_doc());
			}, __("Status"));
			frm.add_custom_button(__("Mark Arrived"), () => {
				frm.call("mark_arrived").then(() => frm.reload_doc());
			}, __("Status"));
		}
		if (frm.doc.total_cbm && frm.doc.max_volume_cbm) {
			const pct = (frm.doc.total_cbm / frm.doc.max_volume_cbm) * 100;
			frm.dashboard.set_headline(
				__("{0}% full — {1} of {2} CBM", [
					pct.toFixed(1),
					frm.doc.total_cbm.toFixed(2),
					frm.doc.max_volume_cbm,
				])
			);
		}
	},

	container_type(frm) {
		const capacity = { "20ft": 33, "40ft": 67, "40ft HC": 76, "45ft": 86, LCL: 0 };
		if (capacity[frm.doc.container_type] !== undefined) {
			frm.set_value("max_volume_cbm", capacity[frm.doc.container_type]);
		}
	},
});
