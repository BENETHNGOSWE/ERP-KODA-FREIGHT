// Kodafreight desk helpers
frappe.provide("kodafreight");

kodafreight.preview_message = function (text, field) {
	if (!text) return;
	frappe.call({
		method: "kodafreight.api.sms.preview_message",
		args: { message: text },
		callback(r) {
			if (r.message && field) {
				field.set_description(
					__("{0} characters · {1} part(s) · {2}", [
						r.message.characters, r.message.parts, r.message.encoding,
					])
				);
			}
		},
	});
};

// Client users get a "My Dashboard" shortcut in the navbar.
$(document).on("app_ready", function () {
	if (frappe.user.has_role("Kodafreight Client Admin") || frappe.user.has_role("Kodafreight SMS Manager")) {
		frappe.ui.toolbar.add_dropdown_button &&
			frappe.ui.toolbar.add_dropdown_button(__("Kodafreight"), __("My SMS Dashboard"), () => {
				window.open("/kodafreight", "_blank");
			});
	}
});
