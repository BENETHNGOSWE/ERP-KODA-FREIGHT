frappe.query_reports["Kodafreight CBM Record"] = {
	filters: [
		{ fieldname: "from_date", label: __("From Date"), fieldtype: "Date" },
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date" },
		{ fieldname: "customer", label: __("Customer"), fieldtype: "Link", options: "Customer" },
		{ fieldname: "container", label: __("Container"), fieldtype: "Link", options: "Kodafreight Container" },
		{
			fieldname: "shipment_status",
			label: __("Shipment Status"),
			fieldtype: "Select",
			options: "\nRegistered\nIn Container\nShipped\nArrived at Godown\nReleased\nDelivered\nCancelled",
		},
	],
};
