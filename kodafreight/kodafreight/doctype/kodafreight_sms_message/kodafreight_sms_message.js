frappe.ui.form.on("Kodafreight SMS Message", {
	refresh(frm) {
		if (frm.is_new()) return;
		if (["Queued", "Draft"].includes(frm.doc.status)) {
			frm.add_custom_button(__("Send Now"), () =>
				frm.call("send_now").then(() => frm.reload_doc())
			).addClass("btn-primary");
		}
		if (["Failed", "Cancelled"].includes(frm.doc.status)) {
			frm.add_custom_button(__("Retry"), () => frm.call("retry").then(() => frm.reload_doc()));
		}
		const colours = {
			Delivered: "green", Sent: "blue", Queued: "orange",
			Sending: "yellow", Failed: "red", Cancelled: "grey", Draft: "grey",
		};
		frm.dashboard.add_indicator(__(frm.doc.status), colours[frm.doc.status] || "grey");
	},
	message(frm) {
		if (!frm.doc.message) return;
		const text = frm.doc.message;
		const gsm = !/[^\x00-\x7F\u00A3\u00A5\u00E8\u00E9]/.test(text);
		const single = gsm ? 160 : 70;
		const multi = gsm ? 153 : 67;
		const parts = text.length <= single ? 1 : Math.ceil(text.length / multi);
		frm.set_df_property(
			"message", "description",
			__("{0} characters · {1} part(s) · {2} encoding", [text.length, parts, gsm ? "GSM7" : "UCS2"])
		);
	},
});
