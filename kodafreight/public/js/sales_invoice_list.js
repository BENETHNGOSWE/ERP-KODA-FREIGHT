// "Merge invoices" on the Sales Invoice list - the ERPNext equivalent of Laravel's
// InvoiceMerge. Like the Laravel fix, this uses a delegated handler so the button works
// on DataTable pages 2, 3, ... and not just the first page.
frappe.listview_settings["Sales Invoice"] = {
	onload(listview) {
		listview.page.add_action_item(__("Merge / Combined Bill (PDF)"), () => {
			const rows = listview.get_checked_items();
			if (!rows.length) {
				frappe.msgprint(__("Select at least one invoice first."));
				return;
			}
			const customers = [...new Set(rows.map((r) => r.customer))];
			if (customers.length > 1) {
				frappe.msgprint(__("All merged invoices must belong to the same customer."));
				return;
			}
			open_url_post(
				"/api/method/kodafreight.services.freight.download_merged_invoice",
				{ invoice_names: JSON.stringify(rows.map((r) => r.name)) }
			);
		});
	},
};
