# Copyright (c) 2026, Trade Hub and contributors
# For license information, please see license.txt

"""
Tenant-Seller Validation Utilities for Trade Hub.

This module provides validation functions to ensure consistency between
tenant and seller fields across documents. It validates that:

1. When both tenant and seller fields are set on a document, they match
2. The seller belongs to the specified tenant

CRITICAL: This validation ensures data integrity by preventing mismatched
tenant-seller relationships which would break tenant isolation.

Usage in hooks.py:
    doc_events = {
        "*": {
            "validate": [
                "tradehub_core.tradehub_core.utils.tenant.validate_tenant",
                "tradehub_core.tradehub_core.utils.tenant_seller_validation.validate_tenant_seller_match"
            ]
        }
    }

DocTypes in tradehub_core with both tenant and seller fields:
- Import Job
- KYC Profile
"""

from typing import Optional

import frappe
from frappe import _

from tradehub_core.tradehub_core.utils.tenant import is_platform_admin


# DocTypes that should be excluded from tenant-seller validation
# These are typically master data or system DocTypes
VALIDATION_EXEMPT_DOCTYPES = frozenset([
    "Tenant",
    "Seller Profile",
    "User",
    "Role",
    "DocType",
    "Custom Field",
    "Property Setter",
])

# Field names that may represent seller on different DocTypes
SELLER_FIELD_NAMES = ("seller", "seller_profile")


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


def _has_tenant_and_seller_fields(doctype: str) -> tuple:
    """
    Check if a DocType has both tenant and seller fields.

    Args:
        doctype: The DocType name to check

    Returns:
        tuple: (has_both, tenant_fieldname, seller_fieldname)
            - has_both: True if DocType has both tenant and a seller field
            - tenant_fieldname: The tenant field name (always "tenant" or None)
            - seller_fieldname: The seller field name or None
    """
    try:
        meta = frappe.get_meta(doctype)
    except Exception:
        return (False, None, None)

    has_tenant = meta.has_field("tenant")
    if not has_tenant:
        return (False, None, None)

    # Check for seller field (could be 'seller' or 'seller_profile')
    seller_fieldname = None
    for field_name in SELLER_FIELD_NAMES:
        if meta.has_field(field_name):
            seller_fieldname = field_name
            break

    if not seller_fieldname:
        return (False, None, None)

    return (True, "tenant", seller_fieldname)


def _get_seller_tenant(seller_name: str) -> Optional[str]:
    """
    Get the tenant associated with a seller.

    Args:
        seller_name: The seller profile name/ID

    Returns:
        str or None: The tenant name if found, None otherwise
    """
    if not seller_name:
        return None

    try:
        return frappe.db.get_value("Seller Profile", seller_name, "tenant")
    except Exception:
        # Seller Profile DocType might not exist or seller not found
        return None


# =============================================================================
# HOOK FUNCTIONS (for use in hooks.py doc_events)
# =============================================================================


def validate_tenant_seller_match(
    doc: "frappe.model.document.Document",
    method: str = None
) -> None:
    """
    Hook function to validate tenant-seller consistency on document save.

    This function ensures that when both tenant and seller fields are set
    on a document, they match (i.e., the seller belongs to the specified tenant).

    This function is designed to be used in hooks.py:
        doc_events = {
            "*": {
                "validate": [
                    "tradehub_core.tradehub_core.utils.tenant.validate_tenant",
                    "tradehub_core.tradehub_core.utils.tenant_seller_validation.validate_tenant_seller_match"
                ]
            }
        }

    Args:
        doc: The document being validated
        method: The hook method name (unused, for hook compatibility)

    Raises:
        frappe.ValidationError: If tenant and seller don't match

    Behavior:
        - Skips exempt DocTypes (system DocTypes)
        - Skips if DocType doesn't have both tenant and seller fields
        - Skips if either tenant or seller is not set (allows partial data)
        - Platform admins bypass this validation
        - If both set and don't match, throws validation error
    """
    # Skip exempt DocTypes
    if doc.doctype in VALIDATION_EXEMPT_DOCTYPES:
        return

    # Check if DocType has both tenant and seller fields
    has_both, tenant_field, seller_field = _has_tenant_and_seller_fields(doc.doctype)
    if not has_both:
        return

    # Get field values
    doc_tenant = doc.get(tenant_field)
    doc_seller = doc.get(seller_field)

    # Skip if either field is not set (allows partial data entry)
    if not doc_tenant or not doc_seller:
        return

    # Platform admins can bypass this validation
    if is_platform_admin():
        return

    # Get the seller's tenant
    seller_tenant = _get_seller_tenant(doc_seller)

    # Handle case where seller has no tenant (data integrity issue)
    if not seller_tenant:
        frappe.throw(
            _("The selected seller ({0}) does not have a tenant assigned. "
              "Please contact your administrator.").format(doc_seller),
            frappe.ValidationError
        )

    # Validate tenant-seller match
    if doc_tenant != seller_tenant:
        frappe.throw(
            _("Tenant and Seller mismatch: The selected seller ({0}) "
              "belongs to tenant '{1}', but this document is assigned to "
              "tenant '{2}'. Please select a seller from the correct tenant "
              "or update the tenant field.").format(
                doc_seller, seller_tenant, doc_tenant
            ),
            frappe.ValidationError
        )


def auto_set_tenant_from_seller(
    doc: "frappe.model.document.Document",
    method: str = None
) -> None:
    """
    Hook function to automatically set tenant from seller if not already set.

    This is useful for before_insert hooks to ensure tenant is populated
    when a seller is selected but tenant is empty.

    Args:
        doc: The document being processed
        method: The hook method name (unused, for hook compatibility)

    Behavior:
        - Skips exempt DocTypes
        - Skips if DocType doesn't have both tenant and seller fields
        - Only sets tenant if it's empty and seller is set
        - Does not override existing tenant values
    """
    # Skip exempt DocTypes
    if doc.doctype in VALIDATION_EXEMPT_DOCTYPES:
        return

    # Check if DocType has both tenant and seller fields
    has_both, tenant_field, seller_field = _has_tenant_and_seller_fields(doc.doctype)
    if not has_both:
        return

    # Get field values
    doc_tenant = doc.get(tenant_field)
    doc_seller = doc.get(seller_field)

    # Only auto-set if tenant is empty and seller is set
    if doc_tenant or not doc_seller:
        return

    # Get the seller's tenant and set it
    seller_tenant = _get_seller_tenant(doc_seller)
    if seller_tenant:
        doc.set(tenant_field, seller_tenant)


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================


def get_sellers_for_tenant(tenant: str = None) -> list:
    """
    Get all sellers belonging to a specific tenant.

    Args:
        tenant: The tenant name. If None, uses current user's tenant.

    Returns:
        list: List of seller names belonging to the tenant

    Example:
        sellers = get_sellers_for_tenant("TEN-00001")
        # Returns: ["SELLER-00001", "SELLER-00002", ...]
    """
    if not tenant:
        from tradehub_core.tradehub_core.utils.tenant import get_current_tenant
        tenant = get_current_tenant()

    if not tenant:
        return []

    try:
        return frappe.get_all(
            "Seller Profile",
            filters={"tenant": tenant},
            pluck="name"
        )
    except Exception:
        # Seller Profile DocType might not exist
        return []


def validate_seller_belongs_to_tenant(seller: str, tenant: str) -> bool:
    """
    Check if a seller belongs to a specific tenant.

    Args:
        seller: The seller profile name/ID
        tenant: The tenant name to check against

    Returns:
        bool: True if seller belongs to tenant, False otherwise

    Example:
        if validate_seller_belongs_to_tenant("SELLER-00001", "TEN-00001"):
            # Seller is valid for this tenant
            pass
    """
    if not seller or not tenant:
        return False

    seller_tenant = _get_seller_tenant(seller)
    return seller_tenant == tenant
