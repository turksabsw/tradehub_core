# Copyright (c) 2024, TradeHub Team and contributors
# For license information, please see license.txt

"""
Tenant-based permission handlers for TradeHub multi-tenant platform.
Provides permission query conditions and permission checks for tenant isolation.

These functions are registered in hooks.py under:
- permission_query_conditions
- has_permission
"""

import frappe
from frappe import _
from frappe.utils import cint, flt

# Import tenant utilities
from tradehub_core.utils.tenant import (
    get_current_tenant,
    is_tenant_admin,
    _has_tenant_field
)


# ---------------------------------------------------------------------------
# ABAC (Attribute-Based Access Control) Policy Constants
# ---------------------------------------------------------------------------

# Financial DocTypes that require KYC verification before access is granted.
# Users without a verified KYC profile will be denied access to these DocTypes.
FINANCIAL_DOCTYPES = frozenset([
    "Payment Intent",
    "Escrow Account",
    "Seller Balance",
    "Commission Plan",
    "Commission Rule",
])

# DocTypes that require AML/sanctions clearance. Users with an AML hit
# ("Hit Found") or sanctions match ("Match Found") on their KYC Profile
# will be blocked from accessing these DocTypes.
AML_SENSITIVE_DOCTYPES = frozenset([
    "Payment Intent",
    "Escrow Account",
    "Seller Balance",
])


def get_tenant_permission_query_conditions(user=None):
    """
    Get SQL WHERE conditions for tenant-based permission filtering.

    This function is called by Frappe when building list queries for DocTypes
    that have it registered in permission_query_conditions in hooks.py.

    Args:
        user (str, optional): User to check permissions for.
            Defaults to current session user.

    Returns:
        str: SQL WHERE clause fragment (without WHERE keyword).
            Returns empty string if no filtering needed.

    Example:
        # In hooks.py
        permission_query_conditions = {
            "Seller Profile": "tradehub_core.permissions.get_tenant_permission_query_conditions",
            "Listing": "tradehub_core.permissions.get_tenant_permission_query_conditions",
        }
    """
    user = user or frappe.session.user

    # System Manager can see all tenants
    if "System Manager" in frappe.get_roles(user):
        return ""

    # Get current tenant context
    tenant = get_current_tenant()

    if not tenant:
        # No tenant context - user might be a guest or not assigned to a tenant
        # Allow access only to own records based on owner
        return f"`tabSeller Profile`.`owner` = '{frappe.db.escape(user)}'"

    # Return tenant filter condition
    return f"`tenant` = '{frappe.db.escape(tenant)}'"


def has_tenant_permission(doc, ptype=None, user=None):
    """
    Check if user has permission to access a document based on tenant isolation.

    This function is called by Frappe when checking document-level permissions
    for DocTypes that have it registered in has_permission in hooks.py.

    Args:
        doc: The document to check permission for.
            Can be a Document object or a dict with doctype and name.
        ptype (str, optional): Permission type ('read', 'write', 'delete', etc.).
            Defaults to 'read'.
        user (str, optional): User to check permissions for.
            Defaults to current session user.

    Returns:
        bool: True if user has permission, False otherwise.

    Example:
        # In hooks.py
        has_permission = {
            "Seller Profile": "tradehub_core.permissions.has_tenant_permission",
            "Listing": "tradehub_core.permissions.has_tenant_permission",
        }
    """
    user = user or frappe.session.user
    ptype = ptype or "read"

    # System Manager has full access
    if "System Manager" in frappe.get_roles(user):
        return True

    # Get document data
    if isinstance(doc, dict):
        doc_tenant = doc.get("tenant")
        doc_owner = doc.get("owner")
        doctype = doc.get("doctype")
    else:
        doc_tenant = getattr(doc, "tenant", None)
        doc_owner = getattr(doc, "owner", None)
        doctype = doc.doctype

    # If DocType doesn't have tenant field, allow based on standard permissions
    if doctype and not _has_tenant_field(doctype):
        return True

    # Get user's tenant
    user_tenant = get_current_tenant()

    # If document has no tenant, check owner
    if not doc_tenant:
        return doc_owner == user

    # If user has no tenant context, deny access to tenant-specific documents
    if not user_tenant:
        return False

    # Check tenant match
    if doc_tenant != user_tenant:
        return False

    # --- ABAC Layer Checks (regulatory, applied before tenant admin bypass) ---

    # KYC verification required for financial DocTypes
    if not _check_kyc_verification(user, doctype):
        return False

    # AML/sanctions check for sensitive DocTypes
    if not _check_aml_sanctions(user, doctype):
        return False

    # Spending limit validation for write/submit operations
    if ptype in ("write", "submit", "create"):
        if not _check_spending_limit(doc, user, ptype):
            return False

    # Tenant admin can perform all operations within their tenant
    if is_tenant_admin(user, user_tenant):
        return True

    # For write/delete operations, additional checks may apply
    if ptype in ("write", "delete", "cancel", "submit"):
        return _has_write_permission_in_tenant(doc, user, ptype)

    return True


def _has_write_permission_in_tenant(doc, user, ptype):
    """
    Check if user has write/delete permission within their tenant.

    Args:
        doc: The document to check.
        user (str): The user.
        ptype (str): Permission type.

    Returns:
        bool: True if user has write permission, False otherwise.
    """
    # Owner always has write permission to their own documents
    doc_owner = getattr(doc, "owner", None) if not isinstance(doc, dict) else doc.get("owner")
    if doc_owner == user:
        return True

    # Check role-based permissions within tenant
    # This integrates with Frappe's standard role permissions
    if isinstance(doc, dict):
        doctype = doc.get("doctype")
    else:
        doctype = doc.doctype

    # Get user roles
    roles = frappe.get_roles(user)

    # Check if any role has the required permission
    permissions = frappe.get_all(
        "DocPerm",
        filters={
            "parent": doctype,
            "role": ["in", roles],
            ptype: 1
        },
        fields=["role"],
        limit=1
    )

    return len(permissions) > 0


# ---------------------------------------------------------------------------
# ABAC Layer Functions
# ---------------------------------------------------------------------------

def _check_kyc_verification(user, doctype):
    """
    ABAC Layer: Deny access to financial DocTypes for non-KYC-verified users.

    Checks the ``kyc_verified`` custom field on the User record.  Only
    DocTypes listed in :data:`FINANCIAL_DOCTYPES` are gated; all other
    DocTypes pass through without a KYC check.

    Args:
        user (str): User to check.
        doctype (str): DocType being accessed.

    Returns:
        bool: True if access is allowed, False if denied.
    """
    if doctype not in FINANCIAL_DOCTYPES:
        return True

    # Use a short-lived cache to avoid repeated DB lookups during a request
    cache_key = f"kyc_verified:{user}"
    kyc_verified = frappe.cache().get_value(cache_key)

    if kyc_verified is None:
        kyc_verified = cint(
            frappe.db.get_value("User", user, "kyc_verified")
        )
        # Cache for 5 minutes — cleared on KYC Profile update
        frappe.cache().set_value(cache_key, kyc_verified, expires_in_sec=300)

    return bool(kyc_verified)


def _check_aml_sanctions(user, doctype):
    """
    ABAC Layer: Block access for users flagged by AML or sanctions screening.

    Looks up the user's active KYC Profile and checks ``aml_check_status``
    and ``sanctions_status``.  A value of ``"Hit Found"`` (AML) or
    ``"Match Found"`` (sanctions) results in access denial for DocTypes
    listed in :data:`AML_SENSITIVE_DOCTYPES`.

    Args:
        user (str): User to check.
        doctype (str): DocType being accessed.

    Returns:
        bool: True if access is allowed, False if blocked.
    """
    if doctype not in AML_SENSITIVE_DOCTYPES:
        return True

    cache_key = f"aml_status:{user}"
    aml_status = frappe.cache().get_value(cache_key)

    if aml_status is None:
        kyc_profile = frappe.db.get_value(
            "KYC Profile",
            {"user": user, "status": ("not in", ["Rejected", "Expired"])},
            ["aml_check_status", "sanctions_status"],
            as_dict=True
        )

        if kyc_profile:
            aml_status = {
                "aml_hit": kyc_profile.aml_check_status == "Hit Found",
                "sanctions_match": kyc_profile.sanctions_status == "Match Found",
            }
        else:
            # No active KYC profile — AML check not applicable
            aml_status = {"aml_hit": False, "sanctions_match": False}

        # Cache for 5 minutes — cleared on KYC Profile update
        frappe.cache().set_value(cache_key, aml_status, expires_in_sec=300)

    if aml_status.get("aml_hit") or aml_status.get("sanctions_match"):
        return False

    return True


def _check_spending_limit(doc, user, ptype):
    """
    ABAC Layer: Enforce spending limits via the Spending Approval Rule DocType.

    For write/submit/create operations on documents that carry an amount,
    this function looks up applicable ``Spending Approval Rule`` records
    for the user's roles.  If the transaction amount falls within a rule's
    range and the user does not hold the required ``approver_role``, access
    is denied (approval is needed).  If the amount exceeds all defined
    limits, access is also denied.

    The check is intentionally lenient: if the Spending Approval Rule
    DocType does not exist yet (pre-migration), or no rules are defined,
    access is allowed.

    Args:
        doc: The document being accessed (Document object or dict).
        user (str): User performing the operation.
        ptype (str): Permission type (``write``, ``submit``, ``create``).

    Returns:
        bool: True if within limits or approved, False if denied.
    """
    if ptype not in ("write", "submit", "create"):
        return True

    # Extract amount from document (check common amount field names)
    if isinstance(doc, dict):
        amount = (
            doc.get("amount")
            or doc.get("total_amount")
            or doc.get("grand_total")
        )
    else:
        amount = (
            getattr(doc, "amount", None)
            or getattr(doc, "total_amount", None)
            or getattr(doc, "grand_total", None)
        )

    if not amount:
        return True

    amount = flt(amount)
    if amount <= 0:
        return True

    # Gracefully handle missing Spending Approval Rule DocType
    try:
        if not frappe.db.exists("DocType", "Spending Approval Rule"):
            return True

        user_roles = frappe.get_roles(user)

        spending_rules = frappe.get_all(
            "Spending Approval Rule",
            filters={
                "is_active": 1,
                "role": ["in", user_roles],
            },
            fields=[
                "role", "min_amount", "max_amount",
                "approver_role", "approval_type",
            ],
            order_by="max_amount asc",
        )

        if not spending_rules:
            return True

        for rule in spending_rules:
            if flt(rule.min_amount) <= amount <= flt(rule.max_amount):
                # Amount falls within this rule's range
                if rule.approver_role and rule.approver_role not in user_roles:
                    # User lacks the approver role — needs approval
                    return False
                # User holds the approver role — allowed
                return True

        # Amount exceeds all defined rule ceilings
        max_allowed = max(flt(r.max_amount) for r in spending_rules)
        if amount > max_allowed:
            return False

    except Exception:
        # Gracefully degrade: if anything fails (e.g., DocType not migrated),
        # allow the operation rather than locking out users.
        pass

    return True


def setup_tenant_permission_query_conditions():
    """
    Build permission query conditions dict for all tenant-aware DocTypes.

    Returns:
        dict: Dictionary mapping DocTypes to permission query condition functions.

    Usage:
        # In hooks.py
        from tradehub_core.permissions import setup_tenant_permission_query_conditions
        permission_query_conditions = setup_tenant_permission_query_conditions()
    """
    tenant_aware_doctypes = get_tenant_aware_doctypes()

    conditions = {}
    for doctype in tenant_aware_doctypes:
        conditions[doctype] = "tradehub_core.permissions.get_tenant_permission_query_conditions"

    return conditions


def setup_has_permission():
    """
    Build has_permission dict for all tenant-aware DocTypes.

    Returns:
        dict: Dictionary mapping DocTypes to has_permission functions.

    Usage:
        # In hooks.py
        from tradehub_core.permissions import setup_has_permission
        has_permission = setup_has_permission()
    """
    tenant_aware_doctypes = get_tenant_aware_doctypes()

    permissions = {}
    for doctype in tenant_aware_doctypes:
        permissions[doctype] = "tradehub_core.permissions.has_tenant_permission"

    return permissions


def get_tenant_aware_doctypes():
    """
    Get list of DocTypes that have tenant isolation enabled.

    Returns:
        list: List of DocType names with tenant field.
    """
    cache_key = "tenant_aware_doctypes"
    doctypes = frappe.cache().get_value(cache_key)

    if doctypes is None:
        # Get all DocTypes with a 'tenant' field
        doctypes = frappe.get_all(
            "DocField",
            filters={
                "fieldname": "tenant",
                "fieldtype": "Link",
                "options": "Tenant"
            },
            pluck="parent",
            distinct=True
        )

        # Also check Custom Fields
        custom_doctypes = frappe.get_all(
            "Custom Field",
            filters={
                "fieldname": "tenant",
                "fieldtype": "Link",
                "options": "Tenant"
            },
            pluck="dt",
            distinct=True
        )

        doctypes = list(set(doctypes + custom_doctypes))

        # Cache for 1 hour
        frappe.cache().set_value(cache_key, doctypes, expires_in_sec=3600)

    return doctypes


def clear_tenant_permission_cache():
    """
    Clear tenant permission related caches.
    Should be called when DocType definitions change.
    """
    frappe.cache().delete_value("tenant_aware_doctypes")

    # Clear individual doctype tenant field cache
    for doctype in frappe.get_all("DocType", pluck="name"):
        frappe.cache().delete_value(f"doctype_has_tenant:{doctype}")


def get_restricted_record_condition(doctype, user=None):
    """
    Get condition for restricting records based on user's tenant and permissions.

    This is a helper function for building complex queries with tenant isolation.

    Args:
        doctype (str): The DocType name.
        user (str, optional): User to check for. Defaults to current user.

    Returns:
        str: SQL condition string.
    """
    user = user or frappe.session.user

    # Build base condition
    conditions = []

    # Add tenant condition
    tenant_condition = get_tenant_permission_query_conditions(user)
    if tenant_condition:
        conditions.append(f"({tenant_condition})")

    # Join conditions
    if conditions:
        return " AND ".join(conditions)

    return ""


def apply_tenant_filter(filters, doctype=None, user=None):
    """
    Apply tenant filter to a filters dictionary.

    Utility function for programmatically adding tenant isolation to queries.

    Args:
        filters (dict): Existing filters dictionary.
        doctype (str, optional): DocType name for tenant field check.
        user (str, optional): User to get tenant for.

    Returns:
        dict: Updated filters with tenant isolation.
    """
    user = user or frappe.session.user

    # System Manager doesn't get filtered
    if "System Manager" in frappe.get_roles(user):
        return filters

    tenant = get_current_tenant()

    if tenant:
        filters = filters or {}
        filters["tenant"] = tenant

    return filters
