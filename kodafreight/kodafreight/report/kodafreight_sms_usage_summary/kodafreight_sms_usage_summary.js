frappe.query_reports["Kodafreight SMS Usage Summary"] = {
	filters: [
		{ fieldname: "client", label: __("Client"), fieldtype: "Link", options: "Kodafreight Client" },
		{ fieldname: "from_date", label: __("From Date"), fieldtype: "Date",
		  default: frappe.datetime.add_days(frappe.datetime.get_today(), -30) },
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date",
		  default: frappe.datetime.get_today() },
		{ fieldname: "sender_name", label: __("Sender Name"), fieldtype: "Link",
		  options: "Kodafreight Sender Name" },
	],
};
