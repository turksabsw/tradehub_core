"""ERPNext Webhook Handlers for TradeHub Core

This module handles reverse synchronization from ERPNext back to TradeHub Core.
When Customer documents are created/updated in ERPNext, these handlers
sync the changes back to the corresponding Buyer Profile DocType in TradeHub.

Events handled:
- Customer: Syncs customer data back to Buyer Profile
"""

import frappe
from frappe import _


def on_customer_update(doc, method):
    """Handle Customer update events from ERPNext.

    Syncs Customer changes back to the corresponding Buyer Profile
    in TradeHub Core when the Customer is updated.

    Args:
        doc: The ERPNext Customer document
        method: The event method name (on_update)
    """
    # Check if this Customer is linked to a TradeHub Buyer Profile
    buyer_profile_id = doc.get("custom_tradehub_buyer_profile_id")
    if not buyer_profile_id:
        # Try to find by matching email or name
        buyer_profile_id = _find_buyer_profile_by_customer(doc)
        if not buyer_profile_id:
            return

    # Check if Buyer Profile DocType exists
    if not frappe.db.exists("Buyer Profile", buyer_profile_id):
        frappe.log_error(
            f"Buyer Profile {buyer_profile_id} not found for Customer {doc.name}",
            "ERPNext Sync Error"
        )
        return

    try:
        # Update the Buyer Profile with changes from Customer
        buyer_profile = frappe.get_doc("Buyer Profile", buyer_profile_id)

        # Sync relevant fields from ERPNext Customer
        if doc.customer_name:
            buyer_profile.company_name = doc.customer_name

        if doc.get("tax_id"):
            buyer_profile.tax_id = doc.tax_id

        # Sync primary address if available
        if doc.get("customer_primary_address"):
            address = frappe.get_doc("Address", doc.customer_primary_address)
            if address:
                buyer_profile.address = address.address_line1
                if address.address_line2:
                    buyer_profile.address += f"\n{address.address_line2}"

        # Sync primary contact phone if available
        if doc.get("customer_primary_contact"):
            contact = frappe.get_doc("Contact", doc.customer_primary_contact)
            if contact and contact.phone:
                buyer_profile.phone = contact.phone

        # Track ERPNext sync metadata
        buyer_profile.erpnext_customer = doc.name
        buyer_profile.last_erpnext_sync = frappe.utils.now()

        buyer_profile.flags.ignore_validate = True
        buyer_profile.save()

        frappe.logger().info(
            f"Synced Customer {doc.name} to Buyer Profile {buyer_profile_id}"
        )

    except Exception as e:
        frappe.log_error(
            f"Failed to sync Customer {doc.name}: {str(e)}",
            "ERPNext Sync Error"
        )


def on_customer_insert(doc, method):
    """Handle Customer insert events from ERPNext.

    When a new Customer is created in ERPNext, this optionally creates
    or links to a corresponding Buyer Profile in TradeHub Core.

    Args:
        doc: The ERPNext Customer document
        method: The event method name (after_insert)
    """
    # Skip if already linked to a Buyer Profile
    if doc.get("custom_tradehub_buyer_profile_id"):
        return

    # Check if auto-creation is enabled in ERPNext Integration Settings
    settings = _get_erpnext_integration_settings()
    if not settings or not settings.get("auto_create_buyer_profiles"):
        return

    try:
        # Check if a Buyer Profile already exists with matching criteria
        existing = _find_buyer_profile_by_customer(doc)
        if existing:
            # Link the existing profile
            doc.db_set("custom_tradehub_buyer_profile_id", existing)
            frappe.logger().info(
                f"Linked Customer {doc.name} to existing Buyer Profile {existing}"
            )
            return

        # Create a new Buyer Profile
        buyer_profile = frappe.new_doc("Buyer Profile")
        buyer_profile.company_name = doc.customer_name
        buyer_profile.status = "Active"
        buyer_profile.erpnext_customer = doc.name

        if doc.get("tax_id"):
            buyer_profile.tax_id = doc.tax_id

        buyer_profile.flags.ignore_validate = True
        buyer_profile.insert()

        # Link back to Customer
        doc.db_set("custom_tradehub_buyer_profile_id", buyer_profile.name)

        frappe.logger().info(
            f"Created Buyer Profile {buyer_profile.name} for Customer {doc.name}"
        )

    except Exception as e:
        frappe.log_error(
            f"Failed to create Buyer Profile for Customer {doc.name}: {str(e)}",
            "ERPNext Sync Error"
        )


def on_customer_delete(doc, method):
    """Handle Customer delete events from ERPNext.

    When a Customer is deleted in ERPNext, this updates the linked
    Buyer Profile status to reflect the deletion.

    Args:
        doc: The ERPNext Customer document
        method: The event method name (on_trash)
    """
    buyer_profile_id = doc.get("custom_tradehub_buyer_profile_id")
    if not buyer_profile_id:
        return

    if not frappe.db.exists("Buyer Profile", buyer_profile_id):
        return

    try:
        # Don't delete the Buyer Profile, just mark it and clear the link
        buyer_profile = frappe.get_doc("Buyer Profile", buyer_profile_id)
        buyer_profile.erpnext_customer = None
        buyer_profile.notes = f"{buyer_profile.notes or ''}\n[ERPNext Customer {doc.name} was deleted on {frappe.utils.now()}]".strip()
        buyer_profile.flags.ignore_validate = True
        buyer_profile.save()

        frappe.logger().info(
            f"Unlinked Buyer Profile {buyer_profile_id} from deleted Customer {doc.name}"
        )

    except Exception as e:
        frappe.log_error(
            f"Failed to update Buyer Profile {buyer_profile_id} on Customer deletion: {str(e)}",
            "ERPNext Sync Error"
        )


def _find_buyer_profile_by_customer(customer_doc):
    """Find a Buyer Profile that matches the given Customer.

    Matching is done by:
    1. Direct link via erpnext_customer field
    2. Company name match
    3. Tax ID match

    Args:
        customer_doc: The ERPNext Customer document

    Returns:
        str: The Buyer Profile name if found, None otherwise
    """
    # Try direct link first
    if frappe.db.exists("Buyer Profile", {"erpnext_customer": customer_doc.name}):
        return frappe.db.get_value("Buyer Profile", {"erpnext_customer": customer_doc.name}, "name")

    # Try company name match
    if customer_doc.customer_name:
        match = frappe.db.get_value(
            "Buyer Profile",
            {"company_name": customer_doc.customer_name},
            "name"
        )
        if match:
            return match

    # Try tax_id match if available
    if customer_doc.get("tax_id"):
        match = frappe.db.get_value(
            "Buyer Profile",
            {"tax_id": customer_doc.tax_id},
            "name"
        )
        if match:
            return match

    return None


def _get_erpnext_integration_settings():
    """Get ERPNext Integration Settings if available.

    Returns:
        dict: Settings document or None if not found
    """
    try:
        if frappe.db.exists("DocType", "ERPNext Integration Settings"):
            return frappe.get_single("ERPNext Integration Settings")
    except Exception:
        pass
    return None
