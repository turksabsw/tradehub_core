# Copyright (c) 2026, Trade Hub and contributors
# For license information, please see license.txt

"""
Tenant Utility Functions for Multi-Tenant Data Isolation in Trade Hub.

This module provides utility functions for managing multi-tenant isolation
across the Trade Hub B2B marketplace. It includes:

- Context management for tenant-aware operations
- Hook functions for automatic tenant assignment and validation
- Permission checks for cross-tenant access prevention
- Caching utilities for tenant settings

CRITICAL: These utilities form the foundation for data isolation. Every DocType
that should be tenant-isolated must use these functions via hooks or explicit calls.

Usage in hooks.py:
    doc_events = {
        "*": {
            "before_insert": "tradehub_core.tradehub_core.utils.tenant.set_tenant",
            "validate": "tradehub_core.tradehub_core.utils.tenant.validate_tenant"
        }
    }

Usage in code:
    from tradehub_core.tradehub_core.utils.tenant import get_current_tenant, tenant_required

    @tenant_required
    def my_tenant_aware_function():
        tenant = get_current_tenant()
        # ... tenant-specific logic
"""

import functools
from typing import Any, Callable, Optional, TypeVar, Union

import frappe
from frappe import _
from frappe.utils import cint

# Type variable for generic function decoration
F = TypeVar("F", bound=Callable[..., Any])

# Cache key prefix for tenant data
TENANT_CACHE_PREFIX = "trade_hub:tenant"

# DocTypes that should be excluded from tenant filtering
# These are typically master data or system DocTypes
TENANT_EXEMPT_DOCTYPES = frozenset([
    "Tenant",
    "User",
    "Role",
    "Role Profile",
    "User Permission",
    "DocType",
    "Custom Field",
    "Property Setter",
    "Country",
    "Currency",
    "Language",
    "Translation",
    "Module Def",
    "Page",
    "Report",
    "Print Format",
    "Print Settings",
    "System Settings",
    "Workflow",
    "Workflow State",
    "Workflow Action",
    "File",
    "Error Log",
    "Activity Log",
    "Comment",
    "Version",
    "Scheduled Job Type",
    "Scheduled Job Log",
    "Email Queue",
    "Email Template",
    "Notification",
])

# Local context storage for tenant information during request lifecycle
_local_tenant_context = {}


# =============================================================================
# CONTEXT MANAGEMENT
# =============================================================================


def get_current_tenant() -> Optional[str]:
    """
    Get the current tenant for the session.

    This function attempts to retrieve the tenant in the following order:
    1. From local request context (set via set_tenant_context)
    2. From the current user's tenant field in User document
    3. From User Permission for Tenant DocType

    Returns:
        str or None: The tenant name if found, None otherwise.

    Example:
        tenant = get_current_tenant()
        if tenant:
            products = frappe.get_all("SKU Product", filters={"tenant": tenant})
    """
    # Check local context first (fastest)
    if frappe.local and hasattr(frappe.local, "tenant"):
        return frappe.local.tenant

    # Check module-level context (for nested operations)
    request_id = id(frappe.local) if frappe.local else None
    if request_id and request_id in _local_tenant_context:
        return _local_tenant_context[request_id]

    # Get from current user
    return get_user_tenant(frappe.session.user)


def get_user_tenant(user: str) -> Optional[str]:
    """
    Get the tenant associated with a specific user.

    This function checks:
    1. The 'tenant' field on the User document (if exists)
    2. User Permission for Tenant DocType

    Args:
        user: The user email/name to check

    Returns:
        str or None: The tenant name if found, None otherwise.

    Example:
        tenant = get_user_tenant("admin@example.com")
    """
    if not user or user in ["Administrator", "Guest"]:
        return None

    # Check cache first
    cache_key = f"{TENANT_CACHE_PREFIX}:user:{user}"
    cached_tenant = frappe.cache().get_value(cache_key)
    if cached_tenant:
        return cached_tenant if cached_tenant != "__NONE__" else None

    tenant = None

    # Try to get from User document's tenant field
    try:
        tenant = frappe.db.get_value("User", user, "tenant")
    except Exception:
        # Field may not exist yet, ignore
        pass

    # If not found, try User Permission
    if not tenant:
        user_permissions = frappe.get_all(
            "User Permission",
            filters={
                "user": user,
                "allow": "Tenant",
                "is_default": 1
            },
            pluck="for_value",
            limit=1
        )
        if user_permissions:
            tenant = user_permissions[0]

    # If still not found, try any Tenant permission (non-default)
    if not tenant:
        user_permissions = frappe.get_all(
            "User Permission",
            filters={
                "user": user,
                "allow": "Tenant"
            },
            pluck="for_value",
            limit=1
        )
        if user_permissions:
            tenant = user_permissions[0]

    # Cache the result (even if None, to avoid repeated lookups)
    frappe.cache().set_value(
        cache_key,
        tenant if tenant else "__NONE__",
        expires_in_sec=300  # 5 minutes
    )

    return tenant


def set_tenant_context(tenant: str) -> None:
    """
    Set the tenant context for the current request/operation.

    This is useful for:
    - Background jobs that need to operate in a specific tenant context
    - API calls that specify a tenant
    - Admin operations on behalf of a tenant

    Args:
        tenant: The tenant name to set as context

    Raises:
        frappe.ValidationError: If tenant doesn't exist

    Example:
        set_tenant_context("TEN-00001")
        try:
            # All subsequent operations will use this tenant
            create_product(...)
        finally:
            clear_tenant_context()
    """
    if tenant:
        # Verify tenant exists
        if not frappe.db.exists("Tenant", tenant):
            frappe.throw(
                _("Tenant {0} does not exist").format(tenant),
                frappe.ValidationError
            )

        # Check if tenant is active
        tenant_status = frappe.db.get_value("Tenant", tenant, "status")
        if tenant_status not in ["Active", "Trial"]:
            frappe.throw(
                _("Tenant {0} is not active (status: {1})").format(
                    tenant, tenant_status
                ),
                frappe.PermissionError
            )

    # Set on frappe.local for quick access
    if frappe.local:
        frappe.local.tenant = tenant

    # Also set in module-level context for nested operations
    request_id = id(frappe.local) if frappe.local else None
    if request_id:
        _local_tenant_context[request_id] = tenant


def clear_tenant_context() -> None:
    """
    Clear the tenant context for the current request.

    Should be called after operations that temporarily set a tenant context,
    typically in a finally block.

    Example:
        set_tenant_context("TEN-00001")
        try:
            do_tenant_operation()
        finally:
            clear_tenant_context()
    """
    if frappe.local and hasattr(frappe.local, "tenant"):
        delattr(frappe.local, "tenant")

    request_id = id(frappe.local) if frappe.local else None
    if request_id and request_id in _local_tenant_context:
        del _local_tenant_context[request_id]


# =============================================================================
# HOOK FUNCTIONS (for use in hooks.py doc_events)
# =============================================================================


def set_tenant(doc: "frappe.model.document.Document", method: str = None) -> None:
    """
    Hook function to automatically set tenant on document before insert.

    This function is designed to be used in hooks.py:
        doc_events = {
            "*": {
                "before_insert": "tradehub_core.tradehub_core.utils.tenant.set_tenant"
            }
        }

    Args:
        doc: The document being inserted
        method: The hook method name (unused, for hook compatibility)

    Behavior:
    - If document already has a tenant set, it's validated but not overwritten
    - If document doesn't have a tenant, it's set from current user's tenant
    - Exempt DocTypes (system DocTypes) are skipped
    """
    # Skip exempt DocTypes
    if doc.doctype in TENANT_EXEMPT_DOCTYPES:
        return

    # Skip if DocType doesn't have tenant field
    meta = frappe.get_meta(doc.doctype)
    if not meta.has_field("tenant"):
        return

    # If tenant is already set, validate it's correct
    if doc.get("tenant"):
        if not is_platform_admin():
            user_tenant = get_current_tenant()
            if user_tenant and doc.tenant != user_tenant:
                frappe.throw(
                    _("Cannot create document for tenant {0}. "
                      "Your tenant is {1}.").format(doc.tenant, user_tenant),
                    frappe.PermissionError
                )
        return

    # Set tenant from current user
    tenant = get_current_tenant()
    if tenant:
        doc.tenant = tenant


def validate_tenant(doc: "frappe.model.document.Document", method: str = None) -> None:
    """
    Hook function to validate tenant isolation on document save.

    This function ensures:
    1. Users can only modify documents belonging to their tenant
    2. Tenant field cannot be changed after creation (unless admin)
    3. Cross-tenant access is prevented

    This function is designed to be used in hooks.py:
        doc_events = {
            "*": {
                "validate": "tradehub_core.tradehub_core.utils.tenant.validate_tenant"
            }
        }

    Args:
        doc: The document being validated
        method: The hook method name (unused, for hook compatibility)

    Raises:
        frappe.PermissionError: If tenant isolation is violated
    """
    # Skip exempt DocTypes
    if doc.doctype in TENANT_EXEMPT_DOCTYPES:
        return

    # Skip if DocType doesn't have tenant field
    meta = frappe.get_meta(doc.doctype)
    if not meta.has_field("tenant"):
        return

    # Platform admins can do anything
    if is_platform_admin():
        return

    # Get current user's tenant
    user_tenant = get_current_tenant()

    # If user has no tenant, they can't modify tenant-isolated documents
    if not user_tenant:
        if doc.get("tenant"):
            frappe.throw(
                _("You do not have access to tenant-isolated documents. "
                  "Please contact your administrator."),
                frappe.PermissionError
            )
        return

    # Check document's tenant matches user's tenant
    if doc.get("tenant") and doc.tenant != user_tenant:
        frappe.throw(
            _("Access denied: You cannot modify documents belonging to "
              "tenant {0}. Your tenant is {1}.").format(doc.tenant, user_tenant),
            frappe.PermissionError
        )

    # For existing documents, prevent tenant field changes
    if not doc.is_new() and doc.has_value_changed("tenant"):
        # Get the original tenant value
        original_tenant = doc.get_db_value("tenant")
        if original_tenant and original_tenant != doc.tenant:
            frappe.throw(
                _("Tenant cannot be changed after document creation. "
                  "Original tenant: {0}").format(original_tenant),
                frappe.PermissionError
            )


# =============================================================================
# PERMISSION CHECKS
# =============================================================================


def is_platform_admin() -> bool:
    """
    Check if the current user is a platform administrator.

    Platform administrators can:
    - Access documents across all tenants
    - Modify system settings
    - Manage tenants

    Returns:
        bool: True if user is a platform admin, False otherwise.
    """
    if frappe.session.user == "Administrator":
        return True

    # Check for System Manager role
    if "System Manager" in frappe.get_roles(frappe.session.user):
        return True

    # Check for specific Trade Hub Admin role (if created)
    if "Trade Hub Admin" in frappe.get_roles(frappe.session.user):
        return True

    return False


def check_tenant_permission(
    tenant: str,
    user: str = None,
    ptype: str = "read"
) -> bool:
    """
    Check if a user has permission to access a specific tenant.

    Args:
        tenant: The tenant name to check
        user: The user to check (defaults to current user)
        ptype: Permission type ('read', 'write', 'create', 'delete')

    Returns:
        bool: True if user has permission, False otherwise.

    Example:
        if check_tenant_permission("TEN-00001", ptype="write"):
            # User can write to this tenant's data
            pass
    """
    if not user:
        user = frappe.session.user

    # Administrators have all permissions
    if user == "Administrator":
        return True

    # System Managers have all permissions
    if "System Manager" in frappe.get_roles(user):
        return True

    # Get user's tenant
    user_tenant = get_user_tenant(user)

    # User can only access their own tenant
    return user_tenant == tenant


# =============================================================================
# TENANT SETTINGS AND UTILITIES
# =============================================================================


def get_tenant_settings(tenant: str = None) -> dict:
    """
    Get settings and configuration for a tenant.

    This includes subscription tier, limits, enabled features, and branding.

    Args:
        tenant: The tenant name (defaults to current tenant)

    Returns:
        dict: Tenant settings including:
            - status: Current status (Active, Trial, etc.)
            - subscription_tier: Tier name
            - limits: max_sellers, max_listings_per_seller, etc.
            - features: Enabled features as boolean flags
            - branding: Logo, colors, etc.

    Raises:
        frappe.DoesNotExistError: If tenant doesn't exist

    Example:
        settings = get_tenant_settings()
        if settings["features"]["api_access"]:
            # API access is enabled for this tenant
            pass
    """
    if not tenant:
        tenant = get_current_tenant()

    if not tenant:
        return {}

    # Check cache first
    cache_key = f"{TENANT_CACHE_PREFIX}:settings:{tenant}"
    cached_settings = frappe.cache().get_value(cache_key)
    if cached_settings:
        return cached_settings

    # Fetch from database
    tenant_doc = frappe.get_doc("Tenant", tenant)

    settings = {
        "name": tenant_doc.name,
        "tenant_name": tenant_doc.tenant_name,
        "company_name": tenant_doc.company_name,
        "status": tenant_doc.status,
        "subscription_tier": tenant_doc.subscription_tier,
        "is_active": tenant_doc.is_active(),
        "limits": {
            "max_sellers": cint(tenant_doc.max_sellers),
            "max_listings_per_seller": cint(tenant_doc.max_listings_per_seller),
            "max_products_per_seller": cint(tenant_doc.max_products_per_seller),
            "commission_rate": tenant_doc.commission_rate or 0,
        },
        "features": {
            "api_access": bool(tenant_doc.has_api_access),
            "analytics": bool(tenant_doc.has_analytics),
            "bulk_import": bool(tenant_doc.has_bulk_import),
            "advanced_reporting": bool(tenant_doc.has_advanced_reporting),
            "priority_support": bool(tenant_doc.has_priority_support),
            "custom_domain": bool(tenant_doc.has_custom_domain),
        },
        "branding": {
            "logo": tenant_doc.logo,
            "favicon": tenant_doc.favicon,
            "primary_color": tenant_doc.primary_color,
            "secondary_color": tenant_doc.secondary_color,
            "custom_domain": tenant_doc.custom_domain if tenant_doc.has_custom_domain else None,
        },
        "defaults": {
            "currency": tenant_doc.currency or "TRY",
            "language": tenant_doc.default_language or "tr",
        }
    }

    # Cache for 5 minutes
    frappe.cache().set_value(cache_key, settings, expires_in_sec=300)

    return settings


def clear_tenant_cache(tenant: str) -> None:
    """
    Clear all cached data for a tenant.

    Should be called when tenant settings are modified.

    Args:
        tenant: The tenant name to clear cache for
    """
    cache_keys = [
        f"{TENANT_CACHE_PREFIX}:settings:{tenant}",
        f"{TENANT_CACHE_PREFIX}:features:{tenant}",
    ]

    for key in cache_keys:
        frappe.cache().delete_value(key)


# =============================================================================
# DECORATORS
# =============================================================================


def tenant_required(func: F) -> F:
    """
    Decorator to require tenant context for a function.

    Use this decorator on functions that must operate within a tenant context.
    If no tenant is available, raises a PermissionError.

    Args:
        func: The function to decorate

    Returns:
        The decorated function

    Raises:
        frappe.PermissionError: If no tenant context is available

    Example:
        @tenant_required
        def create_product(name, price):
            tenant = get_current_tenant()
            # guaranteed to have a tenant here
            ...
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        tenant = get_current_tenant()
        if not tenant:
            frappe.throw(
                _("This operation requires a tenant context. "
                  "Please ensure you are logged in with a tenant-associated user."),
                frappe.PermissionError
            )
        return func(*args, **kwargs)

    return wrapper


# =============================================================================
# QUERY HELPERS
# =============================================================================


def get_tenant_filter(tenant: str = None) -> dict:
    """
    Get a filter dictionary for tenant-isolated queries.

    Args:
        tenant: The tenant to filter by (defaults to current tenant)

    Returns:
        dict: Filter dictionary with tenant field, or empty dict if no tenant

    Example:
        filters = get_tenant_filter()
        filters["status"] = "Active"
        products = frappe.get_all("SKU Product", filters=filters)
    """
    if not tenant:
        tenant = get_current_tenant()

    if tenant:
        return {"tenant": tenant}

    return {}


def add_tenant_filter(filters: dict = None, tenant: str = None) -> dict:
    """
    Add tenant filter to an existing filter dictionary.

    Args:
        filters: Existing filters dictionary (will be modified)
        tenant: The tenant to filter by (defaults to current tenant)

    Returns:
        dict: The filters dictionary with tenant added

    Example:
        filters = {"status": "Active"}
        filters = add_tenant_filter(filters)
        # filters is now {"status": "Active", "tenant": "TEN-00001"}
    """
    if filters is None:
        filters = {}

    if not tenant:
        tenant = get_current_tenant()

    if tenant:
        filters["tenant"] = tenant

    return filters


# =============================================================================
# PERMISSION QUERY CONDITIONS
# =============================================================================


def get_permission_query_conditions(user: str = None) -> str:
    """
    Get SQL conditions for tenant-based permission filtering.

    This function is used with Frappe's permission_query_conditions hook
    to automatically filter queries by tenant.

    Args:
        user: The user to check permissions for (defaults to current user)

    Returns:
        str: SQL condition string for WHERE clause

    Usage in hooks.py:
        permission_query_conditions = {
            "SKU Product": "tradehub_core.tradehub_core.utils.tenant.get_permission_query_conditions"
        }
    """
    if not user:
        user = frappe.session.user

    # Admins see everything
    if is_platform_admin():
        return ""

    # Get user's tenant
    tenant = get_user_tenant(user)

    if not tenant:
        # User without tenant can't see any tenant-isolated data
        return "1=0"

    # Return condition to filter by tenant
    return f"`tabTenant`.name = {frappe.db.escape(tenant)}"


def has_permission(doc: "frappe.model.document.Document", user: str = None) -> bool:
    """
    Check if user has permission to access a document based on tenant.

    This function is used with Frappe's has_permission hook.

    Args:
        doc: The document to check
        user: The user to check (defaults to current user)

    Returns:
        bool: True if user has permission, False otherwise

    Usage in hooks.py:
        has_permission = {
            "SKU Product": "tradehub_core.tradehub_core.utils.tenant.has_permission"
        }
    """
    if not user:
        user = frappe.session.user

    # Admins can access everything
    if is_platform_admin():
        return True

    # Check if document has tenant field
    if not doc.get("tenant"):
        return True

    # Check if user's tenant matches document's tenant
    user_tenant = get_user_tenant(user)
    return user_tenant == doc.tenant


# =============================================================================
# WHITELISTED API FUNCTIONS
# =============================================================================


@frappe.whitelist()
def get_my_tenant() -> Optional[dict]:
    """
    Get the current user's tenant information.

    Returns:
        dict or None: Tenant information including name, settings, and features

    Example API call:
        frappe.call('tradehub_core.tradehub_core.utils.tenant.get_my_tenant')
    """
    tenant = get_current_tenant()
    if not tenant:
        return None

    return get_tenant_settings(tenant)


@frappe.whitelist()
def switch_tenant(tenant_name: str) -> dict:
    """
    Switch the current session to a different tenant (admin only).

    This is useful for administrators who need to operate on behalf of
    different tenants.

    Args:
        tenant_name: The tenant to switch to

    Returns:
        dict: Result with success status and tenant settings

    Raises:
        frappe.PermissionError: If user is not a platform admin
    """
    if not is_platform_admin():
        frappe.throw(
            _("Only platform administrators can switch tenants"),
            frappe.PermissionError
        )

    # Validate tenant exists and is active
    if not frappe.db.exists("Tenant", tenant_name):
        frappe.throw(
            _("Tenant {0} does not exist").format(tenant_name),
            frappe.DoesNotExistError
        )

    set_tenant_context(tenant_name)

    return {
        "success": True,
        "message": _("Switched to tenant {0}").format(tenant_name),
        "tenant": get_tenant_settings(tenant_name)
    }
