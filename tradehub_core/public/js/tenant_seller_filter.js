// Copyright (c) 2026, Trade Hub and contributors
// For license information, please see license.txt

/**
 * Tenant-Seller Bidirectional Filtering Module
 *
 * This module provides centralized client-side filtering for DocTypes that have
 * both tenant and seller fields. It implements:
 *
 * 1. Tenant → Seller filtering: When a tenant is selected, the seller dropdown
 *    is filtered to show only sellers belonging to that tenant.
 *
 * 2. Seller → Tenant auto-fill: When a seller is selected, the tenant field
 *    is automatically populated with the seller's tenant.
 *
 * Supported field names for seller:
 * - 'seller' (e.g., Import Job)
 * - 'seller_profile' (e.g., KYC Profile)
 *
 * This module is loaded globally via app_include_js in hooks.py.
 */

(function() {
    'use strict';

    // Possible field names for seller Link fields
    var SELLER_FIELD_NAMES = ['seller', 'seller_profile'];

    /**
     * Get the seller field name for a given form.
     * Returns the first matching seller field found in the DocType.
     *
     * @param {Object} frm - The Frappe form object
     * @returns {string|null} - The seller field name or null if not found
     */
    function get_seller_field_name(frm) {
        var meta = frappe.get_meta(frm.doctype);
        if (!meta) return null;

        for (var i = 0; i < SELLER_FIELD_NAMES.length; i++) {
            var field_name = SELLER_FIELD_NAMES[i];
            var field = meta.fields.find(function(f) {
                return f.fieldname === field_name &&
                       f.fieldtype === 'Link' &&
                       f.options === 'Seller Profile';
            });
            if (field) {
                return field_name;
            }
        }
        return null;
    }

    /**
     * Check if the DocType has a tenant field.
     *
     * @param {Object} frm - The Frappe form object
     * @returns {boolean} - True if tenant field exists
     */
    function has_tenant_field(frm) {
        var meta = frappe.get_meta(frm.doctype);
        if (!meta) return false;

        return meta.fields.some(function(f) {
            return f.fieldname === 'tenant' &&
                   f.fieldtype === 'Link' &&
                   f.options === 'Tenant';
        });
    }

    /**
     * Set up seller field query filter based on tenant.
     * When a tenant is selected, only sellers belonging to that tenant are shown.
     *
     * @param {Object} frm - The Frappe form object
     * @param {string} seller_field - The seller field name
     */
    function setup_seller_filter(frm, seller_field) {
        frm.set_query(seller_field, function() {
            var filters = {};

            // If tenant is selected, filter sellers by tenant
            if (frm.doc.tenant) {
                filters.tenant = frm.doc.tenant;
            }

            // Only show active sellers
            filters.status = 'Active';

            return {
                filters: filters
            };
        });
    }

    /**
     * Handle tenant field change.
     * Re-applies the seller filter and optionally clears mismatched seller.
     *
     * @param {Object} frm - The Frappe form object
     * @param {string} seller_field - The seller field name
     */
    function on_tenant_change(frm, seller_field) {
        // Refresh the seller field to apply new filter
        frm.refresh_field(seller_field);

        // If seller is already set, validate it matches the new tenant
        if (frm.doc[seller_field] && frm.doc.tenant) {
            frappe.db.get_value('Seller Profile', frm.doc[seller_field], 'tenant', function(r) {
                if (r && r.tenant && r.tenant !== frm.doc.tenant) {
                    // Seller doesn't match tenant - clear the seller field
                    frm.set_value(seller_field, null);
                    frappe.show_alert({
                        message: __('Seller cleared as it does not belong to the selected tenant'),
                        indicator: 'orange'
                    });
                }
            });
        }
    }

    /**
     * Handle seller field change.
     * Auto-fills the tenant field from the seller's tenant.
     *
     * @param {Object} frm - The Frappe form object
     * @param {string} seller_field - The seller field name
     */
    function on_seller_change(frm, seller_field) {
        var seller_value = frm.doc[seller_field];

        if (!seller_value) {
            return;
        }

        // Fetch the seller's tenant and auto-fill
        frappe.db.get_value('Seller Profile', seller_value, 'tenant', function(r) {
            if (r && r.tenant) {
                // Only update if tenant is different or not set
                if (!frm.doc.tenant || frm.doc.tenant !== r.tenant) {
                    frm.set_value('tenant', r.tenant);
                    frappe.show_alert({
                        message: __('Tenant auto-filled from selected seller'),
                        indicator: 'green'
                    });
                }
            } else if (!r || !r.tenant) {
                // Seller has no tenant - warn the user
                frappe.show_alert({
                    message: __('Warning: Selected seller has no tenant assigned'),
                    indicator: 'yellow'
                });
            }
        });
    }

    /**
     * Initialize tenant-seller bidirectional filtering for a form.
     * This function is called on form refresh for all DocTypes.
     *
     * @param {Object} frm - The Frappe form object
     */
    function init_tenant_seller_filter(frm) {
        // Check if this DocType has both tenant and seller fields
        if (!has_tenant_field(frm)) {
            return;
        }

        var seller_field = get_seller_field_name(frm);
        if (!seller_field) {
            return;
        }

        // Set up the seller dropdown filter
        setup_seller_filter(frm, seller_field);
    }

    /**
     * Create dynamic event handlers for a DocType.
     * This attaches tenant and seller change handlers dynamically.
     *
     * @param {string} doctype - The DocType name
     * @param {string} seller_field - The seller field name
     */
    function attach_field_handlers(doctype, seller_field) {
        // Create a handler object for this doctype
        var handlers = {};

        // Refresh handler - set up filters
        handlers.refresh = function(frm) {
            init_tenant_seller_filter(frm);
        };

        // Tenant change handler
        handlers.tenant = function(frm) {
            on_tenant_change(frm, seller_field);
        };

        // Seller change handler
        handlers[seller_field] = function(frm) {
            on_seller_change(frm, seller_field);
        };

        // Register the handlers
        frappe.ui.form.on(doctype, handlers);
    }

    /**
     * Register handlers for known DocTypes with tenant-seller fields.
     * This is called on page load to set up filtering for DocTypes in tradehub_core.
     */
    function register_known_doctypes() {
        // Import Job: has 'seller' field
        attach_field_handlers('Import Job', 'seller');

        // KYC Profile: has 'seller_profile' field
        attach_field_handlers('KYC Profile', 'seller_profile');
    }

    /**
     * Global form refresh hook for dynamic tenant-seller filtering.
     * This provides a fallback for DocTypes not explicitly registered.
     *
     * Note: This is intended for future DocTypes or DocTypes from other apps
     * that may have tenant and seller fields.
     */
    frappe.ui.form.on_refresh_callback = frappe.ui.form.on_refresh_callback || [];
    frappe.ui.form.on_refresh_callback.push(function(frm) {
        // Skip if already handled by explicit registration
        var known_doctypes = ['Import Job', 'KYC Profile'];
        if (known_doctypes.indexOf(frm.doctype) !== -1) {
            return;
        }

        // Check and initialize for other DocTypes dynamically
        if (!has_tenant_field(frm)) {
            return;
        }

        var seller_field = get_seller_field_name(frm);
        if (!seller_field) {
            return;
        }

        // Set up filter
        setup_seller_filter(frm, seller_field);

        // Note: Dynamic field change handlers for unknown DocTypes
        // would require a different approach. For now, we just set up the filter.
        // The Python validation hook will catch any mismatches on save.
    });

    // =========================================================================
    // INITIALIZATION
    // =========================================================================

    // Register handlers when Frappe is ready
    $(document).ready(function() {
        // Wait for frappe to be fully initialized
        if (typeof frappe !== 'undefined' && frappe.ui && frappe.ui.form) {
            register_known_doctypes();
        }
    });

    // Also try to register on frappe.ready if available
    if (typeof frappe !== 'undefined') {
        frappe.ready(function() {
            register_known_doctypes();
        });
    }

})();
