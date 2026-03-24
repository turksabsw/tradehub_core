# Copyright (c) 2026, Trade Hub and contributors
# For license information, please see license.txt

"""
Migration Patch: Migrate Buyer Profile Legacy Flat Address Fields to Address Item Child Table.

This patch migrates data from the deprecated flat address fields on Buyer Profile
to the new Address Item child table structure.

Legacy fields being migrated:
- address_line_1, address_line_2 -> street_address
- city (Data) -> city (Link to City, if matching record found)
- state, country -> notes (as metadata, since Address Item uses Turkish address structure)
- postal_code -> postal_code

Also migrates billing address fields if present:
- billing_address_line_1, billing_address_line_2 -> street_address
- billing_city, billing_state, billing_postal_code, billing_country

IMPORTANT: This patch is idempotent. It skips Buyer Profiles that already
have addresses in the child table.
"""

import frappe
from frappe import _


def execute():
    """
    Main entry point for the migration patch.

    Migrates legacy flat address fields from Buyer Profile to Address Item child table.
    """
    # Reload DocTypes to ensure schema is up to date
    frappe.reload_doc("tradehub_core", "doctype", "address_item")
    frappe.reload_doc("tradehub_core", "doctype", "buyer_profile")

    # Check if Buyer Profile has the legacy address fields
    if not frappe.db.has_column("Buyer Profile", "address_line_1"):
        frappe.log_error(
            title=_("Migration Skip"),
            message=_("Buyer Profile does not have address_line_1 field. Migration skipped.")
        )
        return

    # Get all Buyer Profiles with legacy address data
    buyers_with_legacy_address = get_buyers_with_legacy_address()

    if not buyers_with_legacy_address:
        return

    # Build a city name to ID lookup map for efficient matching
    city_lookup = build_city_lookup()

    migrated_count = 0
    skipped_count = 0

    for buyer_data in buyers_with_legacy_address:
        try:
            if migrate_buyer_address(buyer_data, city_lookup):
                migrated_count += 1
            else:
                skipped_count += 1
        except Exception as e:
            frappe.log_error(
                title=_("Address Migration Error"),
                message=_("Error migrating address for {0}: {1}").format(
                    buyer_data.get("name"), str(e)
                )
            )
            skipped_count += 1

    if migrated_count > 0:
        frappe.db.commit()

    frappe.log_error(
        title=_("Address Migration Complete"),
        message=_("Migrated {0} buyer addresses, skipped {1}").format(
            migrated_count, skipped_count
        )
    )


def get_buyers_with_legacy_address():
    """
    Get all Buyer Profiles that have legacy flat address fields set
    but do not have any addresses in the child table yet.

    Returns:
        list: List of dicts with buyer data including legacy address fields.
    """
    # Get buyers who have legacy address_line_1 set but no child addresses
    buyers = frappe.db.sql(
        """
        SELECT
            bp.name,
            bp.address_line_1,
            bp.address_line_2,
            bp.city,
            bp.state,
            bp.country,
            bp.postal_code,
            bp.billing_address_line_1,
            bp.billing_address_line_2,
            bp.billing_city,
            bp.billing_state,
            bp.billing_country,
            bp.billing_postal_code,
            bp.contact_phone
        FROM `tabBuyer Profile` bp
        WHERE
            bp.address_line_1 IS NOT NULL
            AND bp.address_line_1 != ''
            AND NOT EXISTS (
                SELECT 1 FROM `tabAddress Item` ai
                WHERE ai.parent = bp.name
                AND ai.parenttype = 'Buyer Profile'
            )
        """,
        as_dict=True
    )

    return buyers


def build_city_lookup():
    """
    Build a lookup dictionary mapping city names (lowercase) to City document names.

    Returns:
        dict: Dictionary mapping lowercase city names to City document names.
    """
    # Check if City DocType exists
    if not frappe.db.exists("DocType", "City"):
        return {}

    cities = frappe.get_all(
        "City",
        fields=["name", "city_name"],
        filters={}
    )

    lookup = {}
    for city in cities:
        # Map by city_name (lowercase for case-insensitive matching)
        if city.get("city_name"):
            lookup[city.city_name.lower().strip()] = city.name
        # Also map by document name
        if city.get("name"):
            lookup[city.name.lower().strip()] = city.name

    return lookup


def migrate_buyer_address(buyer_data, city_lookup):
    """
    Migrate a single buyer's legacy address to the Address Item child table.

    Args:
        buyer_data: Dict containing buyer data with legacy address fields.
        city_lookup: Dict mapping city names to City document names.

    Returns:
        bool: True if migration was successful, False if skipped.
    """
    buyer_name = buyer_data.get("name")

    # Get the Buyer Profile document
    buyer_doc = frappe.get_doc("Buyer Profile", buyer_name)

    # Double-check idempotency: skip if already has addresses
    if buyer_doc.get("addresses") and len(buyer_doc.addresses) > 0:
        return False

    addresses_added = False

    # Migrate primary/shipping address
    if buyer_data.get("address_line_1"):
        address_item = create_address_item(
            buyer_data=buyer_data,
            city_lookup=city_lookup,
            address_type="Shipping",
            is_default=True,
            is_billing=False
        )

        if address_item:
            buyer_doc.append("addresses", address_item)
            addresses_added = True

    # Migrate billing address if different from primary
    if buyer_data.get("billing_address_line_1"):
        billing_address_item = create_address_item(
            buyer_data=buyer_data,
            city_lookup=city_lookup,
            address_type="Billing",
            is_default=True,
            is_billing=True
        )

        if billing_address_item:
            buyer_doc.append("addresses", billing_address_item)
            addresses_added = True

    if addresses_added:
        # Save without triggering full validation (we're just adding data)
        buyer_doc.flags.ignore_validate = True
        buyer_doc.flags.ignore_permissions = True
        buyer_doc.save()
        return True

    return False


def create_address_item(buyer_data, city_lookup, address_type, is_default, is_billing):
    """
    Create an Address Item dict from legacy address fields.

    Args:
        buyer_data: Dict containing buyer data with legacy address fields.
        city_lookup: Dict mapping city names to City document names.
        address_type: Type of address ("Shipping" or "Billing").
        is_default: Whether this should be the default address.
        is_billing: Whether to use billing_* fields.

    Returns:
        dict: Address Item data dict, or None if required fields are missing.
    """
    # Get field values based on address type
    if is_billing:
        address_line_1 = buyer_data.get("billing_address_line_1", "")
        address_line_2 = buyer_data.get("billing_address_line_2", "")
        city_value = buyer_data.get("billing_city", "")
        state_value = buyer_data.get("billing_state", "")
        country_value = buyer_data.get("billing_country", "")
        postal_code = buyer_data.get("billing_postal_code", "")
    else:
        address_line_1 = buyer_data.get("address_line_1", "")
        address_line_2 = buyer_data.get("address_line_2", "")
        city_value = buyer_data.get("city", "")
        state_value = buyer_data.get("state", "")
        country_value = buyer_data.get("country", "")
        postal_code = buyer_data.get("postal_code", "")

    # street_address is required - combine address lines
    street_address = address_line_1 or ""
    if address_line_2:
        street_address = f"{street_address}\n{address_line_2}".strip()

    if not street_address:
        return None

    # Try to match city to a City document
    city_link = None
    if city_value and city_lookup:
        city_key = city_value.lower().strip() if isinstance(city_value, str) else ""
        city_link = city_lookup.get(city_key)

    # If no city match found, we still create the address but put city info in notes
    # Address Item requires city field, so we need to find a fallback or skip
    if not city_link:
        # Try to get the first available city as a fallback
        first_city = get_first_city()
        if first_city:
            city_link = first_city
        else:
            # No cities available - cannot create valid Address Item
            # Log and skip this address
            frappe.log_error(
                title=_("Address Migration Warning"),
                message=_("Cannot migrate address for buyer {0}: No matching city found for '{1}' and no fallback cities available").format(
                    buyer_data.get("name"), city_value
                )
            )
            return None

    # Build notes with legacy location data that doesn't fit Address Item structure
    notes_parts = []
    if city_value and not city_lookup.get((city_value or "").lower().strip()):
        notes_parts.append(f"Original City: {city_value}")
    if state_value:
        notes_parts.append(f"State/Province: {state_value}")
    if country_value:
        notes_parts.append(f"Country: {country_value}")

    notes = " | ".join(notes_parts) if notes_parts else ""

    # Create address item dict
    address_item = {
        "address_type": address_type,
        "address_title": f"Migrated {address_type} Address",
        "is_default": 1 if is_default else 0,
        "city": city_link,
        "street_address": street_address,
        "postal_code": postal_code or "",
        "phone": buyer_data.get("contact_phone", "") if not is_billing else "",
        "notes": notes
    }

    return address_item


def get_first_city():
    """
    Get the first available City document as a fallback.

    Returns:
        str or None: City document name, or None if no cities exist.
    """
    cities = frappe.get_all("City", limit=1, pluck="name")
    return cities[0] if cities else None
