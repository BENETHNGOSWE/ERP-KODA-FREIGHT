frappe.ui.form.on("Kodafreight SMS Campaign", {
	refresh(frm) {
		if (frm.is_new()) return;
		if (["Draft", "Partially Failed"].includes(frm.doc.status)) {
			frm.add_custom_button(__("Queue & Send"), () => {
				frappe.confirm(
					__("Send this campaign to {0} recipient(s) using approximately {1} credits?", [
						frm.doc.total_recipients || 0,
						frm.doc.estimated_credits || 0,
					]),
					() => frm.call("queue_campaign").then(() => frm.reload_doc())
				);
			}).addClass("btn-primary");
		}
		if (!["Completed", "Cancelled"].includes(frm.doc.status)) {
			frm.add_custom_button(__("Cancel Campaign"), () =>
				frm.call("cancel_campaign").then(() => frm.reload_doc())
			);
		}
		if (frm.doc.status !== "Draft") {
			frm.add_custom_button(__("View Messages"), () =>
				frappe.set_route("List", "Kodafreight SMS Message", { campaign: frm.doc.name })
			);
		}
	},
	client(frm) {
		frm.set_query("sender_name", () => ({
			query: "kodafreight.api.sms.sender_name_query",
			filters: { client: frm.doc.client },
		}));
	},
	onload(frm) {
		frm.set_query("sender_name", () => ({
			query: "kodafreight.api.sms.sender_name_query",
			filters: { client: frm.doc.client },
		}));
	},
});
