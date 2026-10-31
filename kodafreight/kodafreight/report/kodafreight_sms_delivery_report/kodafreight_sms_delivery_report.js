frappe.query_reports["Kodafreight SMS Delivery Report"] = {
	filters: [
		{ fieldname: "client", label: __("Client"), fieldtype: "Link", options: "Kodafreight Client" },
		{ fieldname: "status", label: __("Status"), fieldtype: "Select",
		  options: ["", "Queued", "Sending", "Sent", "Delivered", "Failed", "Cancelled"] },
		{ fieldname: "from_date", label: __("From Date"), fieldtype: "Date",
		  default: frappe.datetime.add_days(frappe.datetime.get_today(), -7) },
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date",
		  default: frappe.datetime.get_today() },
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "status") {
			const colours = { Delivered: "green", Sent: "blue", Failed: "red", Queued: "orange" };
			const c = colours[data.status] || "grey";
			value = `<span class="indicator-pill ${c}">${data.status}</span>`;
		}
		return value;
	},
};
