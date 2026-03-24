# Copyright (c) 2024, TradeHub Team and contributors
# For license information, please see license.txt

"""
Tenant isolation utilities for multi-tenant TradeHub platform.
Provides functions for tenant context management and validation.
"""

import frappe
from frappe import _
from frappe.utils import cint


def get_current_tenant():
    """
    Get the current tenant from session or user context.

    Returns:
        str: The tenant name/ID if set, None otherwise.

    Example:
        >>> tenant = get_current_tenant()
        >>> if tenant:
        ...     # Do tenant-specific operations
        ...     pass
    """
    # Check if tenant is set in session
    if hasattr(frappe.local, "tenant"):
        return frappe.local.tenant

    # Try to get from session data
    tenant = frappe.session.get("tenant") if hasattr(frappe, "session") else None
    if tenant:
        return tenant

    # Try to get from user's default tenant
    if frappe.session.user and frappe.session.user != "Guest":
        user_tenant = frappe.db.get_value(
            "User",
            frappe.session.user,
            "default_tenant",
            cache=True
        )
        if user_tenant:
            return user_tenant

    return None


def set_tenant(tenant_name):
    """
    Set the current tenant context for the session.

    Args:
        tenant_name (str): The tenant name/ID to set.

    Raises:
        frappe.ValidationError: If tenant does not exist or is disabled.

    Example:
        >>> set_tenant("TENANT-001")
    """
    if not tenant_name:
        clear_tenant_cache()
        return

    # Validate tenant exists and is active
    if not frappe.db.exists("Tenant", tenant_name):
        frappe.throw(
            _("Tenant {0} does not exist").format(tenant_name),
            frappe.DoesNotExistError
        )

    # Check if tenant is enabled
    tenant_doc = frappe.get_cached_doc("Tenant", tenant_name)
    if tenant_doc.get("disabled"):
        frappe.throw(
            _("Tenant {0} is disabled").format(tenant_name),
            frappe.ValidationError
        )

    # Set tenant in local context
    frappe.local.tenant = tenant_name

    # Optionally persist to session
    if hasattr(frappe, "session"):
        frappe.session["tenant"] = tenant_name


def validate_tenant(doc, method=None):
    """
    Validate tenant field on document before save.
    Used as a doc_event handler for wildcard '*' events.

    Args:
        doc: The Frappe document being saved.
        method (str, optional): The doc event method name.

    Note:
        This function should be called from doc_events hooks.
        It ensures tenant isolation by validating tenant field consistency.
    """
    # Skip validation for system documents and non-tenant-aware DocTypes
    if _is_system_doctype(doc.doctype):
        return

    # Check if DocType has tenant field
    if not _has_tenant_field(doc.doctype):
        return

    current_tenant = get_current_tenant()

    # On new document, set tenant if not already set
    if doc.is_new() and not doc.get("tenant"):
        if current_tenant:
            doc.tenant = current_tenant
        return

    # On existing document, validate tenant matches
    if doc.get("tenant") and current_tenant:
        if doc.tenant != current_tenant:
            frappe.throw(
                _("You do not have permission to modify documents from tenant {0}").format(
                    doc.tenant
                ),
                frappe.PermissionError
            )


def clear_tenant_cache():
    """
    Clear tenant-related cache for the current session.
    Should be called when tenant context changes or user logs out.

    Example:
        >>> clear_tenant_cache()
    """
    # Clear local tenant context
    if hasattr(frappe.local, "tenant"):
        delattr(frappe.local, "tenant")

    # Clear session tenant
    if hasattr(frappe, "session") and "tenant" in frappe.session:
        del frappe.session["tenant"]

    # Clear related caches
    tenant = get_current_tenant()
    if tenant:
        cache_keys = [
            f"tenant_config:{tenant}",
            f"tenant_permissions:{tenant}",
            f"tenant_users:{tenant}"
        ]
        for key in cache_keys:
            frappe.cache().delete_value(key)


def get_tenant_users(tenant_name):
    """
    Get list of users belonging to a tenant.

    Args:
        tenant_name (str): The tenant name/ID.

    Returns:
        list: List of user IDs belonging to the tenant.
    """
    if not tenant_name:
        return []

    cache_key = f"tenant_users:{tenant_name}"
    users = frappe.cache().get_value(cache_key)

    if users is None:
        users = frappe.get_all(
            "User",
            filters={"default_tenant": tenant_name, "enabled": 1},
            pluck="name"
        )
        frappe.cache().set_value(cache_key, users, expires_in_sec=3600)

    return users


def is_tenant_admin(user=None, tenant=None):
    """
    Check if user is a tenant administrator.

    Args:
        user (str, optional): User to check. Defaults to current user.
        tenant (str, optional): Tenant to check. Defaults to current tenant.

    Returns:
        bool: True if user is tenant admin, False otherwise.
    """
    user = user or frappe.session.user
    tenant = tenant or get_current_tenant()

    if not tenant:
        return False

    # System Manager has admin access to all tenants
    if "System Manager" in frappe.get_roles(user):
        return True

    # Check tenant-specific admin role
    tenant_admin_role = f"{tenant}_Admin"
    if tenant_admin_role in frappe.get_roles(user):
        return True

    # Check if user is marked as tenant admin
    is_admin = frappe.db.get_value(
        "User",
        user,
        "is_tenant_admin",
        cache=True
    )

    return cint(is_admin) == 1


def _is_system_doctype(doctype):
    """
    Check if DocType is a system DocType that should skip tenant validation.

    Args:
        doctype (str): The DocType name.

    Returns:
        bool: True if system DocType, False otherwise.
    """
    system_doctypes = [
        "User", "Role", "DocType", "DocField", "DocPerm",
        "Custom Field", "Property Setter", "Print Format",
        "Report", "Page", "Module Def", "Workflow",
        "Workflow State", "Workflow Action", "Workflow Transition",
        "Tenant", "Organization"  # Tenant and Organization manage themselves
    ]
    return doctype in system_doctypes


def _has_tenant_field(doctype):
    """
    Check if DocType has a tenant field.

    Args:
        doctype (str): The DocType name.

    Returns:
        bool: True if DocType has tenant field, False otherwise.
    """
    cache_key = f"doctype_has_tenant:{doctype}"
    has_field = frappe.cache().get_value(cache_key)

    if has_field is None:
        meta = frappe.get_meta(doctype)
        has_field = bool(meta.has_field("tenant"))
        frappe.cache().set_value(cache_key, has_field, expires_in_sec=86400)

    return has_field
