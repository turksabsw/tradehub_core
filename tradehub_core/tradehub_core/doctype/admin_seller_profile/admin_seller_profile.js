frappe.listview_settings['Admin Seller Profile'] = {
    refresh: function(listview) {
        if (listview._redirected) return;

        const isAdmin = frappe.user.has_role('System Manager') || frappe.user.has_role('Marketplace Admin');
        const isSeller = frappe.user.has_role('Seller') || frappe.user.has_role('Marketplace Seller');
        if (!isAdmin && isSeller && listview.data && listview.data.length >= 1) {
            listview._redirected = true;
            frappe.set_route('Form', 'Admin Seller Profile', listview.data[0].name);
        }
    }
};

frappe.ui.form.on('Admin Seller Profile', {
    refresh: function(frm) {
        const isAdmin = frappe.user.has_role('System Manager') || frappe.user.has_role('Marketplace Admin');

        if (!isAdmin) {
            // Satıcı: status ve diğer sistem alanları read_only
            frm.set_df_property('status', 'read_only', 1);
            frm.set_df_property('seller_code', 'read_only', 1);
            frm.set_df_property('user', 'read_only', 1);

            // Admin tabını gizle
            frm.set_df_property('tab_admin', 'hidden', 1);
        }
    }
});
