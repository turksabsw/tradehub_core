"""
Migrate Supplier Profile data into Admin Seller Profile.

Supplier Profile DocType is being removed. All relevant fields
(is_verified, verification_type, response_time, response_rate,
on_time_delivery, certifications, review_count) are moved directly
onto Admin Seller Profile.
"""
import frappe


def execute():
    if not frappe.db.table_exists("tabSupplier Profile"):
        return

    admin_sellers = frappe.get_all(
        "Admin Seller Profile",
        fields=["name", "user"],
    )

    for admin in admin_sellers:
        if not admin.user:
            continue

        sp = frappe.db.get_value(
            "Supplier Profile",
            {"user": admin.user},
            [
                "is_verified", "verification_type",
                "response_time", "response_rate", "on_time_delivery",
                "certifications", "review_count",
                "years_in_business", "main_products",
            ],
            as_dict=True,
        )

        if not sp:
            continue

        update_fields = {}

        if sp.is_verified:
            update_fields["is_verified"] = sp.is_verified
        if sp.verification_type:
            update_fields["verification_type"] = sp.verification_type
        if sp.response_time:
            update_fields["response_time"] = sp.response_time
        if sp.response_rate:
            update_fields["response_rate"] = sp.response_rate
        if sp.on_time_delivery:
            update_fields["on_time_delivery"] = sp.on_time_delivery
        if sp.certifications:
            update_fields["certifications"] = sp.certifications
        if sp.review_count:
            update_fields["review_count"] = sp.review_count

        if update_fields:
            frappe.db.set_value("Admin Seller Profile", admin.name, update_fields)

    frappe.db.commit()
