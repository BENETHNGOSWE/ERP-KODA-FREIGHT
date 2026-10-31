frappe.query_reports["Kodafreight Client Credit Statement"] = {
	filters: [
		{ fieldname: "client", label: __("Client"), fieldtype: "Link", options: "Kodafreight Client" },
		{ fieldname: "from_date", label: __("From Date"), fieldtype: "Date",
		  default: frappe.datetime.add_months(frappe.datetime.get_today(), -1) },
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date",
		  default: frappe.datetime.get_today() },
	],
};
