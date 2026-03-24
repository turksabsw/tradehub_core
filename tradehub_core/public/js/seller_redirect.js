frappe.after_ajax(function () {
    frappe.router.on('change', function () {
        var route = frappe.get_route();
        if (
            route && route[0] === 'List' && route[1] === 'Admin Seller Profile' &&
            frappe.user.has_role('Seller') &&
            !frappe.user.has_role('System Manager') &&
            !frappe.user.has_role('Marketplace Admin')
        ) {
            frappe.call({
                method: 'frappe.client.get_list',
                args: { doctype: 'Admin Seller Profile', fields: ['name'], limit: 1 },
                callback: function (r) {
                    if (r.message && r.message.length) {
                        frappe.set_route('Form', 'Admin Seller Profile', r.message[0].name);
                    }
                }
            });
        }
    });
});
