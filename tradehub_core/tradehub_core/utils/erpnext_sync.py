# Copyright (c) 2026, Trade Hub and contributors
# For license information, please see license.txt

"""
ERPNext Sync Utilities for Trade Hub B2B Marketplace.

This module provides bidirectional synchronization between Trade Hub entities
and ERPNext documents. It handles:

- Seller Profile <-> ERPNext Supplier mapping
- Buyer Profile <-> ERPNext Customer mapping
- SKU Product <-> ERPNext Item mapping
- Order <-> ERPNext Sales Order mapping

Key Features:
- Uses Frappe's get_mapped_doc for document mapping
- Supports background job processing via frappe.enqueue
- Provides integration toggle support per tenant
- Handles sync conflicts and error recovery
- Maintains sync status and timestamps

CRITICAL: ERPNext must be installed and configured for sync to work.
Check for ERPNext availability before calling sync functions.

Usage:
    from tradehub_core.tradehub_core.utils.erpnext_sync import sync_seller_to_supplier, sync_sku_to_item

    # Sync a seller to ERPNext Supplier
    supplier_name = sync_seller_to_supplier("SEL-00001")

    # Sync a SKU Product to ERPNext Item
    item_code = sync_sku_to_item("SKU-00001")

    # Sync in background
    frappe.enqueue(
        "tradehub_core.tradehub_core.utils.erpnext_sync.sync_sku_to_item",
        sku_product="SKU-00001",
        queue="short"
    )
"""

from typing import Any, Dict, List, Optional, Union

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, today, now_datetime, getdate


# Cache key prefix for sync data
SYNC_CACHE_PREFIX = "trade_hub:erpnext_sync"

# ERPNext DocTypes we sync with
ERPNEXT_DOCTYPES = {
    "Supplier": "Seller Profile",
    "Customer": "Buyer Profile",
    "Item": "SKU Product",
    "Sales Order": "Order",
}



class ERPNextSyncError(frappe.ValidationError):
    pass



class ERPNextSyncError(frappe.ValidationError):
    pass


# =============================================================================
# ERPNEXT AVAILABILITY CHECKS
# =============================================================================


def is_erpnext_installed() -> bool:
    """
    Check if ERPNext is installed and available.

    Returns:
        bool: True if ERPNext is installed, False otherwise.

    Example:
        if is_erpnext_installed():
            sync_seller_to_supplier(seller_name)
    """
    cache_key = f"{SYNC_CACHE_PREFIX}:erpnext_installed"
    cached_result = frappe.cache().get_value(cache_key)

    if cached_result is not None:
        return cached_result == "1"

    # Check if ERPNext app is installed
    installed = "erpnext" in frappe.get_installed_apps()

    # Cache for 1 hour
    frappe.cache().set_value(cache_key, "1" if installed else "0", expires_in_sec=3600)

    return installed


def check_erpnext_doctype(doctype: str) -> bool:
    """
    Check if a specific ERPNext DocType exists.

    Args:
        doctype: The ERPNext DocType to check

    Returns:
        bool: True if DocType exists, False otherwise.
    """
    return frappe.db.exists("DocType", doctype) is not None


def get_sync_settings(tenant: str = None) -> Dict[str, Any]:
    """
    Get ERPNext sync settings for a tenant.

    Args:
        tenant: The tenant to get settings for (defaults to current tenant)

    Returns:
        dict: Sync settings including enabled flags and mappings
    """
    if not tenant:
        from tradehub_core.tradehub_core.utils.tenant import get_current_tenant
        tenant = get_current_tenant()

    # Check if ERPNext Integration Settings DocType exists
    if not frappe.db.exists("DocType", "ERPNext Integration Settings"):
        # Return default settings if DocType doesn't exist yet
        return {
            "enabled": False,
            "sync_sellers": False,
            "sync_buyers": False,
            "sync_products": False,
            "sync_orders": False,
            "auto_sync": False,
        }

    # Try to get tenant-specific settings
    settings_name = frappe.db.get_value(
        "ERPNext Integration Settings",
        {"tenant": tenant, "enabled": 1}
    )

    if settings_name:
        settings_doc = frappe.get_cached_doc("ERPNext Integration Settings", settings_name)
        return {
            "enabled": settings_doc.enabled,
            "sync_sellers": settings_doc.sync_sellers,
            "sync_buyers": settings_doc.sync_buyers,
            "sync_products": settings_doc.sync_products,
            "sync_orders": settings_doc.sync_orders,
            "auto_sync": settings_doc.auto_sync,
            "default_supplier_group": settings_doc.default_supplier_group,
            "default_customer_group": settings_doc.default_customer_group,
            "default_item_group": settings_doc.default_item_group,
        }

    # Return disabled settings if no settings found
    return {
        "enabled": False,
        "sync_sellers": False,
        "sync_buyers": False,
        "sync_products": False,
        "sync_orders": False,
        "auto_sync": False,
    }


# =============================================================================
# SELLER -> SUPPLIER SYNC
# =============================================================================


def sync_seller_to_supplier(
    seller_name: str,
    force_update: bool = False,
    ignore_permissions: bool = False
) -> Optional[str]:
    """
    Sync a Seller Profile to an ERPNext Supplier.

    This function creates a new Supplier in ERPNext if one doesn't exist,
    or updates an existing Supplier if already linked.

    Args:
        seller_name: Name of the Seller Profile to sync
        force_update: If True, update even if already synced recently
        ignore_permissions: If True, skip permission checks

    Returns:
        str or None: The name of the created/updated Supplier, or None if failed

    Raises:
        frappe.ValidationError: If seller doesn't exist or is not verified
        frappe.AppNotInstalledError: If ERPNext is not installed

    Example:
        supplier_name = sync_seller_to_supplier("SEL-00001")
        if supplier_name:
            print(f"Synced to Supplier: {supplier_name}")
    """
    # Check ERPNext availability
    if not is_erpnext_installed():
        frappe.throw(
            _("ERPNext is not installed. Cannot sync to Supplier."),
            frappe.AppNotInstalledError
        )

    if not check_erpnext_doctype("Supplier"):
        frappe.throw(
            _("Supplier DocType not found. Please ensure ERPNext is properly configured."),
            frappe.ValidationError
        )

    # Get seller document
    if not frappe.db.exists("Seller Profile", seller_name):
        frappe.throw(
            _("Seller Profile {0} not found").format(seller_name),
            frappe.DoesNotExistError
        )

    seller = frappe.get_doc("Seller Profile", seller_name)

    # Check if sync is enabled
    if not seller.sync_with_erpnext:
        frappe.msgprint(
            _("ERPNext sync is not enabled for this seller"),
            indicator="yellow"
        )
        return None

    # Check if seller is verified
    if seller.verification_status != "Verified":
        frappe.throw(
            _("Only verified sellers can be synced to ERPNext. Current status: {0}").format(
                seller.verification_status
            ),
            frappe.ValidationError
        )

    # Check if already linked and not forcing update
    if seller.erpnext_supplier and not force_update:
        # Check if recent sync (within last hour)
        if seller.last_sync_date:
            last_sync = getdate(seller.last_sync_date)
            if last_sync == getdate(today()):
                return seller.erpnext_supplier

    try:
        # Check if Supplier already exists (by tax_id or company_name)
        existing_supplier = None

        if seller.tax_id:
            existing_supplier = frappe.db.get_value(
                "Supplier",
                {"tax_id": seller.tax_id},
                "name"
            )

        if not existing_supplier and seller.company_name:
            existing_supplier = frappe.db.get_value(
                "Supplier",
                {"supplier_name": seller.company_name},
                "name"
            )

        if existing_supplier:
            # Update existing Supplier
            supplier = frappe.get_doc("Supplier", existing_supplier)
            supplier = _update_supplier_from_seller(supplier, seller)
            supplier.save(ignore_permissions=ignore_permissions)
            supplier_name = supplier.name
        else:
            # Create new Supplier
            supplier = _create_supplier_from_seller(seller)
            supplier.insert(ignore_permissions=ignore_permissions)
            supplier_name = supplier.name

        # Update seller with supplier link
        frappe.db.set_value(
            "Seller Profile",
            seller.name,
            {
                "erpnext_supplier": supplier_name,
                "last_sync_date": now_datetime()
            },
            update_modified=False
        )

        # Log sync
        _log_sync_event("Seller Profile", seller.name, "Supplier", supplier_name, "create" if not existing_supplier else "update")

        return supplier_name

    except Exception as e:
        # Log error
        frappe.log_error(
            message=str(e),
            title=f"ERPNext Sync Error: Seller {seller_name}"
        )
        raise


def _create_supplier_from_seller(seller: Document) -> Document:
    """
    Create a new Supplier document from Seller Profile data.

    Args:
        seller: The Seller Profile document

    Returns:
        Document: The new Supplier document (not yet inserted)
    """
    # Get sync settings for default values
    sync_settings = get_sync_settings(seller.tenant)
    default_group = sync_settings.get("default_supplier_group", "All Supplier Groups")

    # Determine supplier type
    supplier_type = "Company"
    if seller.business_type in ["Individual", "Sole Proprietorship"]:
        supplier_type = "Individual"

    supplier = frappe.new_doc("Supplier")
    supplier.supplier_name = seller.company_name or seller.seller_name
    supplier.supplier_type = supplier_type
    supplier.supplier_group = default_group

    # Basic info
    if seller.country:
        supplier.country = seller.country
    if seller.tax_id:
        supplier.tax_id = seller.tax_id

    # Website
    if seller.website:
        supplier.website = seller.website

    # Address and contact will be created separately if needed
    # This is handled by ERPNext's standard behavior

    # Custom fields mapping (if exists)
    _map_custom_fields(seller, supplier, "seller_to_supplier")

    return supplier


def _update_supplier_from_seller(supplier: Document, seller: Document) -> Document:
    """
    Update an existing Supplier document from Seller Profile data.

    Args:
        supplier: The existing Supplier document
        seller: The Seller Profile document

    Returns:
        Document: The updated Supplier document (not yet saved)
    """
    # Update fields that should sync
    supplier.supplier_name = seller.company_name or seller.seller_name

    if seller.country:
        supplier.country = seller.country
    if seller.tax_id:
        supplier.tax_id = seller.tax_id
    if seller.website:
        supplier.website = seller.website

    # Update custom fields
    _map_custom_fields(seller, supplier, "seller_to_supplier")

    return supplier


def sync_supplier_to_seller(
    supplier_name: str,
    seller_name: str = None,
    ignore_permissions: bool = False
) -> Optional[str]:
    """
    Reverse sync: Update Seller Profile from ERPNext Supplier changes.

    This is typically called from ERPNext hooks when a Supplier is updated.

    Args:
        supplier_name: Name of the ERPNext Supplier
        seller_name: Name of the linked Seller Profile (optional, will be looked up)
        ignore_permissions: If True, skip permission checks

    Returns:
        str or None: The name of the updated Seller Profile, or None if no link found
    """
    if not is_erpnext_installed():
        return None

    if not frappe.db.exists("Supplier", supplier_name):
        return None

    # Find linked seller
    if not seller_name:
        seller_name = frappe.db.get_value(
            "Seller Profile",
            {"erpnext_supplier": supplier_name},
            "name"
        )

    if not seller_name:
        # No linked seller found
        return None

    try:
        supplier = frappe.get_doc("Supplier", supplier_name)
        seller = frappe.get_doc("Seller Profile", seller_name)

        # Update seller fields from supplier
        if supplier.supplier_name:
            seller.company_name = supplier.supplier_name
        if supplier.country:
            seller.country = supplier.country
        if supplier.tax_id:
            seller.tax_id = supplier.tax_id
        if supplier.website:
            seller.website = supplier.website

        seller.last_sync_date = now_datetime()
        seller.save(ignore_permissions=ignore_permissions)

        # Log reverse sync
        _log_sync_event("Supplier", supplier_name, "Seller Profile", seller_name, "reverse_sync")

        return seller_name

    except Exception as e:
        frappe.log_error(
            message=str(e),
            title=f"ERPNext Reverse Sync Error: Supplier {supplier_name}"
        )
        return None


# =============================================================================
# BUYER -> CUSTOMER SYNC
# =============================================================================


def sync_buyer_to_customer(
    buyer_name: str,
    force_update: bool = False,
    ignore_permissions: bool = False
) -> Optional[str]:
    """
    Sync a Buyer Profile to an ERPNext Customer.

    This function creates a new Customer in ERPNext if one doesn't exist,
    or updates an existing Customer if already linked.

    Args:
        buyer_name: Name of the Buyer Profile to sync
        force_update: If True, update even if already synced recently
        ignore_permissions: If True, skip permission checks

    Returns:
        str or None: The name of the created/updated Customer, or None if failed

    Raises:
        frappe.ValidationError: If buyer doesn't exist or is not verified
        frappe.AppNotInstalledError: If ERPNext is not installed

    Example:
        customer_name = sync_buyer_to_customer("BUY-00001")
        if customer_name:
            print(f"Synced to Customer: {customer_name}")
    """
    # Check ERPNext availability
    if not is_erpnext_installed():
        frappe.throw(
            _("ERPNext is not installed. Cannot sync to Customer."),
            frappe.AppNotInstalledError
        )

    if not check_erpnext_doctype("Customer"):
        frappe.throw(
            _("Customer DocType not found. Please ensure ERPNext is properly configured."),
            frappe.ValidationError
        )

    # Get buyer document
    if not frappe.db.exists("Buyer Profile", buyer_name):
        frappe.throw(
            _("Buyer Profile {0} not found").format(buyer_name),
            frappe.DoesNotExistError
        )

    buyer = frappe.get_doc("Buyer Profile", buyer_name)

    # Check if sync is enabled
    if not buyer.get("sync_with_erpnext"):
        frappe.msgprint(
            _("ERPNext sync is not enabled for this buyer"),
            indicator="yellow"
        )
        return None

    # Check if buyer is verified (or allow unverified if needed)
    if buyer.verification_status != "Verified":
        # For buyers, we might allow syncing unverified ones
        # This depends on business requirements
        pass

    # Check if already linked and not forcing update
    if buyer.get("erpnext_customer") and not force_update:
        if buyer.get("last_sync_date"):
            last_sync = getdate(buyer.last_sync_date)
            if last_sync == getdate(today()):
                return buyer.erpnext_customer

    try:
        # Check if Customer already exists
        existing_customer = None

        if buyer.get("tax_id"):
            existing_customer = frappe.db.get_value(
                "Customer",
                {"tax_id": buyer.tax_id},
                "name"
            )

        if not existing_customer and buyer.company_name:
            existing_customer = frappe.db.get_value(
                "Customer",
                {"customer_name": buyer.company_name},
                "name"
            )

        if existing_customer:
            # Update existing Customer
            customer = frappe.get_doc("Customer", existing_customer)
            customer = _update_customer_from_buyer(customer, buyer)
            customer.save(ignore_permissions=ignore_permissions)
            customer_name = customer.name
        else:
            # Create new Customer
            customer = _create_customer_from_buyer(buyer)
            customer.insert(ignore_permissions=ignore_permissions)
            customer_name = customer.name

        # Update buyer with customer link
        frappe.db.set_value(
            "Buyer Profile",
            buyer.name,
            {
                "erpnext_customer": customer_name,
                "last_sync_date": now_datetime()
            },
            update_modified=False
        )

        # Log sync
        _log_sync_event("Buyer Profile", buyer.name, "Customer", customer_name, "create" if not existing_customer else "update")

        return customer_name

    except Exception as e:
        frappe.log_error(
            message=str(e),
            title=f"ERPNext Sync Error: Buyer {buyer_name}"
        )
        raise


def _create_customer_from_buyer(buyer: Document) -> Document:
    """
    Create a new Customer document from Buyer Profile data.

    Args:
        buyer: The Buyer Profile document

    Returns:
        Document: The new Customer document (not yet inserted)
    """
    # Get sync settings for default values
    sync_settings = get_sync_settings(buyer.tenant)
    default_group = sync_settings.get("default_customer_group", "All Customer Groups")

    # Determine customer type
    customer_type = "Company"
    if buyer.get("buyer_type") in ["Individual", "Sole Proprietorship"]:
        customer_type = "Individual"

    customer = frappe.new_doc("Customer")
    customer.customer_name = buyer.company_name or buyer.buyer_name
    customer.customer_type = customer_type
    customer.customer_group = default_group

    # Territory mapping
    if buyer.get("country"):
        # Try to find matching territory
        territory = frappe.db.get_value("Territory", {"territory_name": buyer.country})
        if territory:
            customer.territory = territory
        else:
            customer.territory = "All Territories"
    else:
        customer.territory = "All Territories"

    # Tax ID
    if buyer.get("tax_id"):
        customer.tax_id = buyer.tax_id

    # Website
    if buyer.get("website"):
        customer.website = buyer.website

    # Custom fields mapping
    _map_custom_fields(buyer, customer, "buyer_to_customer")

    return customer


def _update_customer_from_buyer(customer: Document, buyer: Document) -> Document:
    """
    Update an existing Customer document from Buyer Profile data.

    Args:
        customer: The existing Customer document
        buyer: The Buyer Profile document

    Returns:
        Document: The updated Customer document (not yet saved)
    """
    # Update fields that should sync
    customer.customer_name = buyer.company_name or buyer.buyer_name

    if buyer.get("tax_id"):
        customer.tax_id = buyer.tax_id
    if buyer.get("website"):
        customer.website = buyer.website

    # Update custom fields
    _map_custom_fields(buyer, customer, "buyer_to_customer")

    return customer


def sync_customer_to_buyer(
    customer_name: str,
    buyer_name: str = None,
    ignore_permissions: bool = False
) -> Optional[str]:
    """
    Reverse sync: Update Buyer Profile from ERPNext Customer changes.

    This is typically called from ERPNext hooks when a Customer is updated.

    Args:
        customer_name: Name of the ERPNext Customer
        buyer_name: Name of the linked Buyer Profile (optional, will be looked up)
        ignore_permissions: If True, skip permission checks

    Returns:
        str or None: The name of the updated Buyer Profile, or None if no link found
    """
    if not is_erpnext_installed():
        return None

    if not frappe.db.exists("Customer", customer_name):
        return None

    # Find linked buyer
    if not buyer_name:
        buyer_name = frappe.db.get_value(
            "Buyer Profile",
            {"erpnext_customer": customer_name},
            "name"
        )

    if not buyer_name:
        return None

    try:
        customer = frappe.get_doc("Customer", customer_name)
        buyer = frappe.get_doc("Buyer Profile", buyer_name)

        # Update buyer fields from customer
        if customer.customer_name:
            buyer.company_name = customer.customer_name
        if customer.get("tax_id"):
            buyer.tax_id = customer.tax_id
        if customer.get("website"):
            buyer.website = customer.website

        buyer.last_sync_date = now_datetime()
        buyer.save(ignore_permissions=ignore_permissions)

        # Log reverse sync
        _log_sync_event("Customer", customer_name, "Buyer Profile", buyer_name, "reverse_sync")

        return buyer_name

    except Exception as e:
        frappe.log_error(
            message=str(e),
            title=f"ERPNext Reverse Sync Error: Customer {customer_name}"
        )
        return None



# =============================================================================
# LISTING -> ITEM SYNC
# =============================================================================


def create_item_from_listing(listing: Document) -> str:
    """
    Create an ERPNext Item from a Listing.
    
    Args:
        listing: The Listing document
        
    Returns:
        str: The name (item_code) of the created Item
    """
    if not is_erpnext_installed():
        return None
        
    # Logic similar to make_item_from_sku but for Listing
    # For now, we will create a basic item 
    
    item_code = listing.listing_code
    
    if frappe.db.exists("Item", item_code):
        return item_code
        
    item = frappe.new_doc("Item")
    item.item_code = item_code
    item.item_name = listing.title
    item.item_group = "All Item Groups"
    item.description = listing.description
    item.standard_rate = listing.selling_price
    item.stock_uom = listing.stock_uom or "Nos"
    item.is_stock_item = 1
    
    if listing.image:
        item.image = listing.image
        
    item.insert(ignore_permissions=True)
    
    return item.item_code


def sync_item_from_listing(listing: Document) -> bool:
    """
    Sync Listing data to linked ERPNext Item.
    
    Args:
        listing: The Listing document
        
    Returns:
        bool: True if sync successful
    """
    if not listing.erpnext_item:
        return False
        
    if not frappe.db.exists("Item", listing.erpnext_item):
        return False
        
    try:
        item = frappe.get_doc("Item", listing.erpnext_item)
        item.item_name = listing.title
        item.description = listing.description
        item.standard_rate = listing.selling_price
        
        if listing.image:
            item.image = listing.image
            
        item.save(ignore_permissions=True)
        return True
    except Exception as e:
        frappe.log_error(f"Failed to sync Listing {listing.name} to Item: {str(e)}")
        return False



# =============================================================================
# LISTING -> ITEM SYNC
# =============================================================================


def create_item_from_listing(listing: Document) -> str:
    """
    Create an ERPNext Item from a Listing.
    
    Args:
        listing: The Listing document
        
    Returns:
        str: The name (item_code) of the created Item
    """
    if not is_erpnext_installed():
        return None
        
    # Logic similar to make_item_from_sku but for Listing
    
    item_code = listing.listing_code
    
    if frappe.db.exists("Item", item_code):
        return item_code
        
    item = frappe.new_doc("Item")
    item.item_code = item_code
    item.item_name = listing.title
    item.item_group = "All Item Groups"
    item.description = listing.description
    item.standard_rate = listing.selling_price
    item.stock_uom = listing.stock_uom or "Nos"
    item.is_stock_item = 1
    
    if listing.image:
        item.image = listing.image
        
    item.insert(ignore_permissions=True)
    
    return item.item_code


def sync_item_from_listing(listing: Document) -> bool:
    """
    Sync Listing data to linked ERPNext Item.
    
    Args:
        listing: The Listing document
        
    Returns:
        bool: True if sync successful
    """
    if not listing.erpnext_item:
        return False
        
    if not frappe.db.exists("Item", listing.erpnext_item):
        return False
        
    try:
        item = frappe.get_doc("Item", listing.erpnext_item)
        item.item_name = listing.title
        item.description = listing.description
        item.standard_rate = listing.selling_price
        
        if listing.image:
            item.image = listing.image
            
        item.save(ignore_permissions=True)
        return True
    except Exception as e:
        frappe.log_error(f"Failed to sync Listing {listing.name} to Item: {str(e)}")
        return False


# =============================================================================
# SKU PRODUCT -> ITEM SYNC
# =============================================================================


def make_item_from_sku(source_name: str, target_doc: Document = None) -> Document:
    """
    Create an ERPNext Item from a SKU Product using get_mapped_doc.

    This function uses Frappe's get_mapped_doc utility for field mapping
    between Trade Hub SKU Product and ERPNext Item.

    Args:
        source_name: Name of the SKU Product
        target_doc: Existing Item to update (optional)

    Returns:
        Document: The mapped Item document (not yet inserted)

    Example:
        item = make_item_from_sku("SKU-00001")
        item.insert()
    """
    from frappe.model.mapper import get_mapped_doc

    def set_missing_values(source, target):
        """Set default values and calculate fields."""
        # Set item_code from SKU code if not already set
        if not target.item_code:
            target.item_code = source.sku_code

        # Set item_name from product name
        if not target.item_name:
            target.item_name = source.product_name

        # Set default item group
        if not target.item_group:
            sync_settings = get_sync_settings(source.tenant)
            default_group = sync_settings.get("default_item_group", "All Item Groups")

            # Try to map category to item group
            if source.category:
                # Look for matching Item Group by name
                item_group = frappe.db.get_value(
                    "Item Group",
                    {"item_group_name": source.category_name},
                    "name"
                )
                if item_group:
                    target.item_group = item_group
                else:
                    target.item_group = default_group
            else:
                target.item_group = default_group

        # Set stock UOM
        if not target.stock_uom and source.stock_uom:
            target.stock_uom = source.stock_uom
        elif not target.stock_uom:
            target.stock_uom = "Nos"

        # Set valuation rate from base price
        if source.base_price and not target.valuation_rate:
            target.valuation_rate = source.base_price

        # Set standard rate (selling price)
        if source.base_price:
            target.standard_rate = source.base_price

        # Set opening stock if stock quantity is positive
        if source.stock_quantity and flt(source.stock_quantity) > 0:
            target.opening_stock = flt(source.stock_quantity)

        # Set is_stock_item
        target.is_stock_item = 1 if source.is_stock_item else 0

        # Set physical attributes
        if source.weight:
            target.weight_per_unit = flt(source.weight)
        if source.weight_uom:
            target.weight_uom = source.weight_uom

    return get_mapped_doc(
        "SKU Product",
        source_name,
        {
            "SKU Product": {
                "doctype": "Item",
                "field_map": {
                    "sku_code": "item_code",
                    "product_name": "item_name",
                    "short_description": "description",
                    "stock_uom": "stock_uom",
                    "is_stock_item": "is_stock_item",
                    "brand_name": "brand",
                    "country_of_origin": "country_of_origin",
                    "hs_code": "customs_tariff_number",
                    "thumbnail": "image",
                    "weight": "weight_per_unit",
                    "weight_uom": "weight_uom",
                },
            }
        },
        target_doc,
        set_missing_values
    )


def sync_sku_to_item(
    sku_product: str,
    force_update: bool = False,
    ignore_permissions: bool = False
) -> Optional[str]:
    """
    Sync a SKU Product to an ERPNext Item.

    This function creates a new Item in ERPNext if one doesn't exist,
    or updates an existing Item if already linked.

    Args:
        sku_product: Name of the SKU Product to sync
        force_update: If True, update even if already synced recently
        ignore_permissions: If True, skip permission checks

    Returns:
        str or None: The name (item_code) of the created/updated Item, or None if failed

    Raises:
        frappe.ValidationError: If SKU product doesn't exist or is not active
        frappe.AppNotInstalledError: If ERPNext is not installed

    Example:
        item_code = sync_sku_to_item("SKU-00001")
        if item_code:
            print(f"Synced to Item: {item_code}")
    """
    # Check ERPNext availability
    if not is_erpnext_installed():
        frappe.throw(
            _("ERPNext is not installed. Cannot sync to Item."),
            frappe.AppNotInstalledError
        )

    if not check_erpnext_doctype("Item"):
        frappe.throw(
            _("Item DocType not found. Please ensure ERPNext is properly configured."),
            frappe.ValidationError
        )

    # Get SKU product document
    if not frappe.db.exists("SKU Product", sku_product):
        frappe.throw(
            _("SKU Product {0} not found").format(sku_product),
            frappe.DoesNotExistError
        )

    sku = frappe.get_doc("SKU Product", sku_product)

    # Check if sync is enabled
    if not sku.sync_with_erpnext:
        frappe.msgprint(
            _("ERPNext sync is not enabled for this SKU Product"),
            indicator="yellow"
        )
        return None

    # Check if SKU is in valid status for sync
    valid_statuses = ["Active", "Passive"]
    if sku.status not in valid_statuses:
        frappe.throw(
            _("Only Active or Passive SKU Products can be synced to ERPNext. Current status: {0}").format(
                sku.status
            ),
            frappe.ValidationError
        )

    # Check sync settings
    sync_settings = get_sync_settings(sku.tenant)
    if not sync_settings.get("enabled") or not sync_settings.get("sync_products"):
        # Allow sync if explicitly requested even if settings disabled
        pass

    # Check if already linked and not forcing update
    if sku.erpnext_item_code and not force_update:
        # Check if Item still exists
        if frappe.db.exists("Item", sku.erpnext_item_code):
            return sku.erpnext_item_code

    try:
        # Check if Item already exists (by item_code = sku_code)
        existing_item = frappe.db.get_value(
            "Item",
            {"item_code": sku.sku_code},
            "name"
        )

        if not existing_item:
            # Also check by item_name
            existing_item = frappe.db.get_value(
                "Item",
                {"item_name": sku.product_name},
                "name"
            )

        if existing_item:
            # Update existing Item
            item = frappe.get_doc("Item", existing_item)
            item = _update_item_from_sku(item, sku)
            item.save(ignore_permissions=ignore_permissions)
            item_code = item.item_code
        else:
            # Create new Item using make_item_from_sku
            item = make_item_from_sku(sku_product)
            item.insert(ignore_permissions=ignore_permissions)
            item_code = item.item_code

        # Update SKU product with item link
        frappe.db.set_value(
            "SKU Product",
            sku.name,
            {
                "erpnext_item_code": item_code,
            },
            update_modified=False
        )

        # Log sync
        _log_sync_event("SKU Product", sku.name, "Item", item_code, "create" if not existing_item else "update")

        return item_code

    except Exception as e:
        # Log error
        frappe.log_error(
            message=str(e),
            title=f"ERPNext Sync Error: SKU Product {sku_product}"
        )
        raise


def _create_item_from_sku(sku: Document) -> Document:
    """
    Create a new Item document from SKU Product data.

    Args:
        sku: The SKU Product document

    Returns:
        Document: The new Item document (not yet inserted)
    """
    # Get sync settings for default values
    sync_settings = get_sync_settings(sku.tenant)
    default_group = sync_settings.get("default_item_group", "All Item Groups")

    item = frappe.new_doc("Item")
    item.item_code = sku.sku_code
    item.item_name = sku.product_name
    item.description = sku.short_description or sku.product_name

    # Set item group from category mapping or default
    if sku.category:
        item_group = frappe.db.get_value(
            "Item Group",
            {"item_group_name": sku.category_name},
            "name"
        )
        item.item_group = item_group or default_group
    else:
        item.item_group = default_group

    # Stock settings
    item.stock_uom = sku.stock_uom or "Nos"
    item.is_stock_item = 1 if sku.is_stock_item else 0

    # Pricing
    if sku.base_price:
        item.standard_rate = flt(sku.base_price)
        item.valuation_rate = flt(sku.base_price)

    # Physical attributes
    if sku.weight:
        item.weight_per_unit = flt(sku.weight)
    if sku.weight_uom:
        item.weight_uom = sku.weight_uom

    # Trade info
    if sku.country_of_origin:
        item.country_of_origin = sku.country_of_origin
    if sku.hs_code:
        item.customs_tariff_number = sku.hs_code

    # Brand
    if sku.brand_name:
        # Check if Brand exists in ERPNext
        if frappe.db.exists("Brand", sku.brand_name):
            item.brand = sku.brand_name

    # Image
    if sku.thumbnail:
        item.image = sku.thumbnail

    # Custom fields mapping
    _map_custom_fields(sku, item, "sku_to_item")

    return item


def _update_item_from_sku(item: Document, sku: Document) -> Document:
    """
    Update an existing Item document from SKU Product data.

    Args:
        item: The existing Item document
        sku: The SKU Product document

    Returns:
        Document: The updated Item document (not yet saved)
    """
    # Update basic fields
    item.item_name = sku.product_name
    item.description = sku.short_description or sku.product_name

    # Update stock settings
    item.is_stock_item = 1 if sku.is_stock_item else 0
    if sku.stock_uom:
        item.stock_uom = sku.stock_uom

    # Update pricing
    if sku.base_price:
        item.standard_rate = flt(sku.base_price)

    # Update physical attributes
    if sku.weight:
        item.weight_per_unit = flt(sku.weight)
    if sku.weight_uom:
        item.weight_uom = sku.weight_uom

    # Update trade info
    if sku.country_of_origin:
        item.country_of_origin = sku.country_of_origin
    if sku.hs_code:
        item.customs_tariff_number = sku.hs_code

    # Update brand
    if sku.brand_name and frappe.db.exists("Brand", sku.brand_name):
        item.brand = sku.brand_name

    # Update image
    if sku.thumbnail:
        item.image = sku.thumbnail

    # Update custom fields
    _map_custom_fields(sku, item, "sku_to_item")

    return item


def sync_item_to_sku(
    item_code: str,
    sku_product: str = None,
    ignore_permissions: bool = False
) -> Optional[str]:
    """
    Reverse sync: Update SKU Product from ERPNext Item changes.

    This is typically called from ERPNext hooks when an Item is updated.

    Args:
        item_code: The ERPNext Item code
        sku_product: Name of the linked SKU Product (optional, will be looked up)
        ignore_permissions: If True, skip permission checks

    Returns:
        str or None: The name of the updated SKU Product, or None if no link found
    """
    if not is_erpnext_installed():
        return None

    if not frappe.db.exists("Item", item_code):
        return None

    # Find linked SKU product
    if not sku_product:
        sku_product = frappe.db.get_value(
            "SKU Product",
            {"erpnext_item_code": item_code},
            "name"
        )

    if not sku_product:
        # Try to find by SKU code matching item_code
        sku_product = frappe.db.get_value(
            "SKU Product",
            {"sku_code": item_code},
            "name"
        )

    if not sku_product:
        # No linked SKU found
        return None

    try:
        item = frappe.get_doc("Item", item_code)
        sku = frappe.get_doc("SKU Product", sku_product)

        # Update SKU fields from Item
        if item.item_name:
            sku.product_name = item.item_name
        if item.description:
            sku.short_description = item.description

        # Update stock settings
        sku.is_stock_item = 1 if item.is_stock_item else 0
        if item.stock_uom:
            sku.stock_uom = item.stock_uom

        # Update pricing
        if item.standard_rate:
            sku.base_price = flt(item.standard_rate)

        # Update physical attributes
        if item.get("weight_per_unit"):
            sku.weight = flt(item.weight_per_unit)
        if item.get("weight_uom"):
            sku.weight_uom = item.weight_uom

        # Update trade info
        if item.get("country_of_origin"):
            sku.country_of_origin = item.country_of_origin
        if item.get("customs_tariff_number"):
            sku.hs_code = item.customs_tariff_number

        # Update image
        if item.image:
            sku.thumbnail = item.image

        # Ensure ERPNext link is set
        sku.erpnext_item_code = item_code

        sku.save(ignore_permissions=ignore_permissions)

        # Log reverse sync
        _log_sync_event("Item", item_code, "SKU Product", sku_product, "reverse_sync")

        return sku_product

    except Exception as e:
        frappe.log_error(
            message=str(e),
            title=f"ERPNext Reverse Sync Error: Item {item_code}"
        )
        return None


def bulk_sync_skus_to_items(
    tenant: str = None,
    limit: int = 100,
    force_update: bool = False
) -> Dict[str, Any]:
    """
    Bulk sync all eligible SKU Products to ERPNext Items.

    Args:
        tenant: Optional tenant filter
        limit: Maximum number of SKUs to sync
        force_update: If True, update even if recently synced

    Returns:
        dict: Sync results with counts and errors
    """
    if not is_erpnext_installed():
        return {"success": False, "error": "ERPNext not installed", "synced": 0, "failed": 0}

    filters = {
        "status": ["in", ["Active", "Passive"]],
        "sync_with_erpnext": 1
    }

    if not force_update:
        # Only sync products not yet linked
        filters["erpnext_item_code"] = ["is", "not set"]

    if tenant:
        filters["tenant"] = tenant

    skus = frappe.get_all(
        "SKU Product",
        filters=filters,
        fields=["name"],
        limit_page_length=limit,
        order_by="modified desc"
    )

    results = {
        "success": True,
        "total": len(skus),
        "synced": 0,
        "failed": 0,
        "errors": []
    }

    for sku in skus:
        try:
            sync_sku_to_item(sku.name, force_update=force_update)
            results["synced"] += 1
        except Exception as e:
            results["failed"] += 1
            results["errors"].append({
                "sku_product": sku.name,
                "error": str(e)
            })

    return results


# =============================================================================
# ORDER -> SALES ORDER SYNC
# =============================================================================


def make_sales_order(source_name: str, target_doc: Document = None) -> Document:
    """
    Create an ERPNext Sales Order from a Trade Hub Order using get_mapped_doc.

    This function uses Frappe's get_mapped_doc utility for field mapping
    between Trade Hub Order and ERPNext Sales Order.

    Args:
        source_name: Name of the Trade Hub Order
        target_doc: Existing Sales Order to update (optional)

    Returns:
        Document: The mapped Sales Order document (not yet inserted)

    Example:
        sales_order = make_sales_order("ORD-00001")
        sales_order.insert()
    """
    from frappe.model.mapper import get_mapped_doc

    def set_missing_values(source, target):
        """Set default values and calculate totals."""
        # Get Customer from Buyer Profile
        buyer = frappe.get_doc("Buyer Profile", source.buyer)
        if buyer.get("erpnext_customer"):
            target.customer = buyer.erpnext_customer
        else:
            # Try to sync buyer first if not already synced
            customer_name = sync_buyer_to_customer(source.buyer, ignore_permissions=True)
            if customer_name:
                target.customer = customer_name

        # Set delivery date if available
        if source.estimated_delivery_date:
            target.delivery_date = source.estimated_delivery_date
        else:
            # Default to order_date + delivery_days or 30 days
            target.delivery_date = frappe.utils.add_days(
                source.order_date,
                source.delivery_days or 30
            )

        # Set company (required for Sales Order)
        if not target.company:
            target.company = frappe.defaults.get_user_default("Company") or frappe.db.get_single_value("Global Defaults", "default_company")

        # Calculate totals
        target.run_method("set_missing_values")
        target.run_method("calculate_taxes_and_totals")

    def update_item(source_item, target_item, source_parent):
        """Map item fields and set item_code from SKU Product."""
        # Get Item Code from SKU Product
        if source_item.sku_product:
            sku_product = frappe.get_doc("SKU Product", source_item.sku_product)
            if sku_product.get("erpnext_item"):
                target_item.item_code = sku_product.erpnext_item
            else:
                # Use SKU code as item_code if no ERPNext Item linked
                target_item.item_code = sku_product.sku_code

        # Set delivery date for item
        if source_parent.estimated_delivery_date:
            target_item.delivery_date = source_parent.estimated_delivery_date
        else:
            target_item.delivery_date = frappe.utils.add_days(
                source_parent.order_date,
                source_parent.delivery_days or 30
            )

    return get_mapped_doc(
        "Order",
        source_name,
        {
            "Order": {
                "doctype": "Sales Order",
                "field_map": {
                    "order_date": "transaction_date",
                    "currency": "currency",
                    "total_amount": "grand_total",
                    "buyer_notes": "terms",
                    "seller_notes": "notes",
                },
                "validation": {
                    "docstatus": ["=", 0]
                }
            },
            "Order Item": {
                "doctype": "Sales Order Item",
                "field_map": {
                    "quantity": "qty",
                    "unit_price": "rate",
                    "uom": "uom",
                    "item_description": "description",
                    "amount": "amount",
                },
                "postprocess": update_item
            }
        },
        target_doc,
        set_missing_values
    )


def sync_order_to_sales_order(
    order_name: str,
    force_update: bool = False,
    ignore_permissions: bool = False,
    submit: bool = False
) -> Optional[str]:
    """
    Sync a Trade Hub Order to an ERPNext Sales Order.

    This function creates a new Sales Order in ERPNext if one doesn't exist,
    or updates an existing Sales Order if already linked.

    Args:
        order_name: Name of the Trade Hub Order to sync
        force_update: If True, update even if already synced recently
        ignore_permissions: If True, skip permission checks
        submit: If True, submit the Sales Order after creation

    Returns:
        str or None: The name of the created/updated Sales Order, or None if failed

    Raises:
        frappe.ValidationError: If order doesn't exist or conditions not met
        frappe.AppNotInstalledError: If ERPNext is not installed

    Example:
        sales_order_name = sync_order_to_sales_order("ORD-00001")
        if sales_order_name:
            print(f"Synced to Sales Order: {sales_order_name}")
    """
    # Check ERPNext availability
    if not is_erpnext_installed():
        frappe.throw(
            _("ERPNext is not installed. Cannot sync to Sales Order."),
            frappe.AppNotInstalledError
        )

    if not check_erpnext_doctype("Sales Order"):
        frappe.throw(
            _("Sales Order DocType not found. Please ensure ERPNext is properly configured."),
            frappe.ValidationError
        )

    # Get order document
    if not frappe.db.exists("Order", order_name):
        frappe.throw(
            _("Order {0} not found").format(order_name),
            frappe.DoesNotExistError
        )

    order = frappe.get_doc("Order", order_name)

    # Check sync settings
    sync_settings = get_sync_settings(order.tenant)
    if not sync_settings.get("enabled") or not sync_settings.get("sync_orders"):
        frappe.msgprint(
            _("Order sync is not enabled for this tenant"),
            indicator="yellow"
        )
        return None

    # Check order status - only sync confirmed orders
    valid_statuses = ["Confirmed", "Processing", "Ready to Ship", "Shipped", "Delivered", "Completed"]
    if order.status not in valid_statuses:
        frappe.throw(
            _("Only confirmed orders can be synced to ERPNext. Current status: {0}").format(
                order.status
            ),
            frappe.ValidationError
        )

    # Check if already linked and not forcing update
    if order.linked_sales_order and not force_update:
        # Check if Sales Order still exists
        if frappe.db.exists("Sales Order", order.linked_sales_order):
            return order.linked_sales_order

    try:
        # Check if Sales Order already exists for this order
        existing_sales_order = frappe.db.get_value(
            "Sales Order",
            {"po_no": order_name},  # Use PO reference to track Trade Hub Order
            "name"
        )

        if existing_sales_order:
            # Update existing Sales Order
            sales_order = frappe.get_doc("Sales Order", existing_sales_order)
            if sales_order.docstatus == 0:
                # Only update if not submitted
                sales_order = _update_sales_order_from_order(sales_order, order)
                sales_order.save(ignore_permissions=ignore_permissions)
            sales_order_name = sales_order.name
        else:
            # Create new Sales Order using make_sales_order
            sales_order = make_sales_order(order_name)

            # Set PO reference to track this Trade Hub Order
            sales_order.po_no = order_name

            sales_order.insert(ignore_permissions=ignore_permissions)
            sales_order_name = sales_order.name

            # Submit if requested and order is in appropriate state
            if submit and order.status in ["Processing", "Ready to Ship", "Shipped"]:
                sales_order.submit()

        # Update order with Sales Order link
        frappe.db.set_value(
            "Order",
            order.name,
            {
                "linked_sales_order": sales_order_name,
            },
            update_modified=False
        )

        # Log sync
        _log_sync_event("Order", order.name, "Sales Order", sales_order_name, "create" if not existing_sales_order else "update")

        return sales_order_name

    except Exception as e:
        # Log error
        frappe.log_error(
            message=str(e),
            title=f"ERPNext Sync Error: Order {order_name}"
        )
        raise


def _update_sales_order_from_order(sales_order: Document, order: Document) -> Document:
    """
    Update an existing Sales Order document from Trade Hub Order data.

    Args:
        sales_order: The existing Sales Order document
        order: The Trade Hub Order document

    Returns:
        Document: The updated Sales Order document (not yet saved)
    """
    # Update header fields
    sales_order.transaction_date = order.order_date
    sales_order.currency = order.currency

    if order.estimated_delivery_date:
        sales_order.delivery_date = order.estimated_delivery_date

    # Update notes
    if order.buyer_notes:
        sales_order.terms = order.buyer_notes
    if order.seller_notes:
        sales_order.notes = order.seller_notes

    # Update items if order is not submitted
    if sales_order.docstatus == 0:
        # Clear existing items and re-add
        sales_order.items = []

        for item in order.items:
            so_item = sales_order.append("items", {})
            so_item.qty = item.quantity
            so_item.rate = item.unit_price
            so_item.uom = item.uom or "Nos"
            so_item.description = item.item_description

            if order.estimated_delivery_date:
                so_item.delivery_date = order.estimated_delivery_date

            # Get Item Code from SKU Product
            if item.sku_product:
                sku_product = frappe.get_doc("SKU Product", item.sku_product)
                if sku_product.get("erpnext_item"):
                    so_item.item_code = sku_product.erpnext_item
                else:
                    so_item.item_code = sku_product.sku_code

    return sales_order


def sync_sales_order_to_order(
    sales_order_name: str,
    order_name: str = None,
    ignore_permissions: bool = False
) -> Optional[str]:
    """
    Reverse sync: Update Trade Hub Order from ERPNext Sales Order changes.

    This is typically called from ERPNext hooks when a Sales Order is updated.

    Args:
        sales_order_name: Name of the ERPNext Sales Order
        order_name: Name of the linked Trade Hub Order (optional, will be looked up)
        ignore_permissions: If True, skip permission checks

    Returns:
        str or None: The name of the updated Order, or None if no link found
    """
    if not is_erpnext_installed():
        return None

    if not frappe.db.exists("Sales Order", sales_order_name):
        return None

    # Find linked order
    if not order_name:
        # Try to find by linked_sales_order field
        order_name = frappe.db.get_value(
            "Order",
            {"linked_sales_order": sales_order_name},
            "name"
        )

    if not order_name:
        # Try to find by PO reference
        sales_order = frappe.get_doc("Sales Order", sales_order_name)
        if sales_order.po_no:
            order_name = frappe.db.get_value(
                "Order",
                {"name": sales_order.po_no},
                "name"
            )

    if not order_name:
        # No linked order found
        return None

    try:
        sales_order = frappe.get_doc("Sales Order", sales_order_name)
        order = frappe.get_doc("Order", order_name)

        # Update order fields from Sales Order
        # Map Sales Order status to Trade Hub Order status
        status_map = {
            "Draft": "Draft",
            "To Deliver and Bill": "Confirmed",
            "To Bill": "Delivered",
            "To Deliver": "Confirmed",
            "Completed": "Completed",
            "Cancelled": "Cancelled",
            "Closed": "Completed"
        }

        if sales_order.status in status_map and order.status not in ["Completed", "Cancelled", "Refunded"]:
            new_status = status_map.get(sales_order.status)
            if new_status and new_status != order.status:
                order.status = new_status

        # Update payment status if Sales Order is billed
        if sales_order.per_billed == 100:
            order.payment_status = "Fully Paid"
        elif sales_order.per_billed > 0:
            order.payment_status = "Partially Paid"

        order.save(ignore_permissions=ignore_permissions)

        # Log reverse sync
        _log_sync_event("Sales Order", sales_order_name, "Order", order_name, "reverse_sync")

        return order_name

    except Exception as e:
        frappe.log_error(
            message=str(e),
            title=f"ERPNext Reverse Sync Error: Sales Order {sales_order_name}"
        )
        return None


def bulk_sync_orders_to_sales_orders(
    tenant: str = None,
    limit: int = 100,
    force_update: bool = False
) -> Dict[str, Any]:
    """
    Bulk sync all eligible orders to ERPNext Sales Orders.

    Args:
        tenant: Optional tenant filter
        limit: Maximum number of orders to sync
        force_update: If True, update even if recently synced

    Returns:
        dict: Sync results with counts and errors
    """
    if not is_erpnext_installed():
        return {"success": False, "error": "ERPNext not installed", "synced": 0, "failed": 0}

    filters = {
        "status": ["in", ["Confirmed", "Processing", "Ready to Ship", "Shipped", "Delivered", "Completed"]],
        "linked_sales_order": ["is", "not set"]
    }

    if tenant:
        filters["tenant"] = tenant

    orders = frappe.get_all(
        "Order",
        filters=filters,
        fields=["name"],
        limit_page_length=limit,
        order_by="modified desc"
    )

    results = {
        "success": True,
        "total": len(orders),
        "synced": 0,
        "failed": 0,
        "errors": []
    }

    for order in orders:
        try:
            sync_order_to_sales_order(order.name, force_update=force_update)
            results["synced"] += 1
        except Exception as e:
            results["failed"] += 1
            results["errors"].append({
                "order": order.name,
                "error": str(e)
            })

    return results


# =============================================================================
# BULK SYNC OPERATIONS
# =============================================================================


def bulk_sync_sellers_to_suppliers(
    tenant: str = None,
    limit: int = 100,
    force_update: bool = False
) -> Dict[str, Any]:
    """
    Bulk sync all eligible sellers to ERPNext Suppliers.

    Args:
        tenant: Optional tenant filter
        limit: Maximum number of sellers to sync
        force_update: If True, update even if recently synced

    Returns:
        dict: Sync results with counts and errors
    """
    if not is_erpnext_installed():
        return {"success": False, "error": "ERPNext not installed", "synced": 0, "failed": 0}

    filters = {
        "verification_status": "Verified",
        "sync_with_erpnext": 1,
        "status": "Active"
    }

    if tenant:
        filters["tenant"] = tenant

    sellers = frappe.get_all(
        "Seller Profile",
        filters=filters,
        fields=["name"],
        limit_page_length=limit,
        order_by="modified desc"
    )

    results = {
        "success": True,
        "total": len(sellers),
        "synced": 0,
        "failed": 0,
        "errors": []
    }

    for seller in sellers:
        try:
            sync_seller_to_supplier(seller.name, force_update=force_update)
            results["synced"] += 1
        except Exception as e:
            results["failed"] += 1
            results["errors"].append({
                "seller": seller.name,
                "error": str(e)
            })

    return results


def bulk_sync_buyers_to_customers(
    tenant: str = None,
    limit: int = 100,
    force_update: bool = False
) -> Dict[str, Any]:
    """
    Bulk sync all eligible buyers to ERPNext Customers.

    Args:
        tenant: Optional tenant filter
        limit: Maximum number of buyers to sync
        force_update: If True, update even if recently synced

    Returns:
        dict: Sync results with counts and errors
    """
    if not is_erpnext_installed():
        return {"success": False, "error": "ERPNext not installed", "synced": 0, "failed": 0}

    filters = {
        "verification_status": "Verified",
        "status": "Active"
    }

    # Check if sync_with_erpnext field exists
    meta = frappe.get_meta("Buyer Profile")
    if meta.has_field("sync_with_erpnext"):
        filters["sync_with_erpnext"] = 1

    if tenant:
        filters["tenant"] = tenant

    buyers = frappe.get_all(
        "Buyer Profile",
        filters=filters,
        fields=["name"],
        limit_page_length=limit,
        order_by="modified desc"
    )

    results = {
        "success": True,
        "total": len(buyers),
        "synced": 0,
        "failed": 0,
        "errors": []
    }

    for buyer in buyers:
        try:
            sync_buyer_to_customer(buyer.name, force_update=force_update)
            results["synced"] += 1
        except Exception as e:
            results["failed"] += 1
            results["errors"].append({
                "buyer": buyer.name,
                "error": str(e)
            })

    return results


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


def _map_custom_fields(source: Document, target: Document, mapping_type: str) -> None:
    """
    Map custom fields between source and target documents.

    This function handles any custom field mappings defined in the
    ERPNext Integration Settings.

    Args:
        source: Source document
        target: Target document
        mapping_type: Type of mapping (e.g., "seller_to_supplier")
    """
    # Get custom field mappings from settings
    # This would be configured in ERPNext Integration Settings
    # For now, just handle standard mappings

    # Add custom mappings here as needed
    pass


def _log_sync_event(
    source_doctype: str,
    source_name: str,
    target_doctype: str,
    target_name: str,
    action: str
) -> None:
    """
    Log a sync event for auditing.

    Args:
        source_doctype: Source DocType
        source_name: Source document name
        target_doctype: Target DocType
        target_name: Target document name
        action: Action performed (create, update, reverse_sync)
    """
    # Log to activity log or custom sync log
    frappe.get_doc({
        "doctype": "Comment",
        "comment_type": "Info",
        "reference_doctype": source_doctype,
        "reference_name": source_name,
        "content": _("ERPNext Sync: {0} {1} to {2}").format(
            action.replace("_", " ").title(),
            target_doctype,
            target_name
        )
    }).insert(ignore_permissions=True)


def get_sync_status(doctype: str, docname: str) -> Dict[str, Any]:
    """
    Get the ERPNext sync status for a document.

    Args:
        doctype: The DocType (Seller Profile, Buyer Profile, SKU Product, or Order)
        docname: The document name

    Returns:
        dict: Sync status including linked ERPNext document and last sync date
    """
    if not frappe.db.exists(doctype, docname):
        return {"synced": False, "error": "Document not found"}

    doc = frappe.get_doc(doctype, docname)

    if doctype == "Seller Profile":
        return {
            "synced": bool(doc.get("erpnext_supplier")),
            "erpnext_doctype": "Supplier",
            "erpnext_name": doc.get("erpnext_supplier"),
            "last_sync_date": doc.get("last_sync_date"),
            "sync_enabled": doc.get("sync_with_erpnext")
        }
    elif doctype == "Buyer Profile":
        return {
            "synced": bool(doc.get("erpnext_customer")),
            "erpnext_doctype": "Customer",
            "erpnext_name": doc.get("erpnext_customer"),
            "last_sync_date": doc.get("last_sync_date"),
            "sync_enabled": doc.get("sync_with_erpnext")
        }
    elif doctype == "SKU Product":
        return {
            "synced": bool(doc.get("erpnext_item_code")),
            "erpnext_doctype": "Item",
            "erpnext_name": doc.get("erpnext_item_code"),
            "sku_status": doc.status,
            "sync_enabled": doc.get("sync_with_erpnext"),
            "can_sync": doc.status in ["Active", "Passive"]
        }
    elif doctype == "Order":
        return {
            "synced": bool(doc.get("linked_sales_order")),
            "erpnext_doctype": "Sales Order",
            "erpnext_name": doc.get("linked_sales_order"),
            "order_status": doc.status,
            "can_sync": doc.status in ["Confirmed", "Processing", "Ready to Ship", "Shipped", "Delivered", "Completed"]
        }

    return {"synced": False, "error": "Unsupported DocType"}


def unlink_erpnext_document(doctype: str, docname: str) -> bool:
    """
    Unlink an ERPNext document from a Trade Hub document.

    This removes the link but does not delete the ERPNext document.

    Args:
        doctype: The Trade Hub DocType
        docname: The document name

    Returns:
        bool: True if unlinked successfully
    """
    if not frappe.db.exists(doctype, docname):
        return False

    if doctype == "Seller Profile":
        frappe.db.set_value(
            "Seller Profile",
            docname,
            {"erpnext_supplier": None, "last_sync_date": None},
            update_modified=False
        )
        return True
    elif doctype == "Buyer Profile":
        frappe.db.set_value(
            "Buyer Profile",
            docname,
            {"erpnext_customer": None, "last_sync_date": None},
            update_modified=False
        )
        return True
    elif doctype == "SKU Product":
        frappe.db.set_value(
            "SKU Product",
            docname,
            {"erpnext_item_code": None},
            update_modified=False
        )
        return True
    elif doctype == "Order":
        frappe.db.set_value(
            "Order",
            docname,
            {"linked_sales_order": None},
            update_modified=False
        )
        return True

    return False


# =============================================================================
# WHITELISTED API FUNCTIONS
# =============================================================================


@frappe.whitelist()
def sync_seller(seller_name: str, force: bool = False) -> Dict[str, Any]:
    """
    API endpoint to sync a seller to ERPNext Supplier.

    Args:
        seller_name: Name of the Seller Profile
        force: Force update even if recently synced

    Returns:
        dict: Sync result
    """
    try:
        supplier_name = sync_seller_to_supplier(seller_name, force_update=cint(force))
        return {
            "success": True,
            "supplier": supplier_name,
            "message": _("Successfully synced to Supplier {0}").format(supplier_name)
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


@frappe.whitelist()
def sync_buyer(buyer_name: str, force: bool = False) -> Dict[str, Any]:
    """
    API endpoint to sync a buyer to ERPNext Customer.

    Args:
        buyer_name: Name of the Buyer Profile
        force: Force update even if recently synced

    Returns:
        dict: Sync result
    """
    try:
        customer_name = sync_buyer_to_customer(buyer_name, force_update=cint(force))
        return {
            "success": True,
            "customer": customer_name,
            "message": _("Successfully synced to Customer {0}").format(customer_name)
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


@frappe.whitelist()
def get_erpnext_sync_status(doctype: str, docname: str) -> Dict[str, Any]:
    """
    API endpoint to get ERPNext sync status.

    Args:
        doctype: The DocType to check
        docname: The document name

    Returns:
        dict: Sync status
    """
    return get_sync_status(doctype, docname)


@frappe.whitelist()
def bulk_sync_all(
    sync_type: str,
    tenant: str = None,
    limit: int = 100
) -> Dict[str, Any]:
    """
    API endpoint for bulk sync operations.

    Args:
        sync_type: Type of sync ("sellers", "buyers", "skus", or "orders")
        tenant: Optional tenant filter
        limit: Maximum items to sync

    Returns:
        dict: Sync results
    """
    if not frappe.has_permission("System Manager"):
        frappe.throw(_("Only System Managers can perform bulk sync"), frappe.PermissionError)

    if sync_type == "sellers":
        return bulk_sync_sellers_to_suppliers(tenant=tenant, limit=cint(limit))
    elif sync_type == "buyers":
        return bulk_sync_buyers_to_customers(tenant=tenant, limit=cint(limit))
    elif sync_type == "skus":
        return bulk_sync_skus_to_items(tenant=tenant, limit=cint(limit))
    elif sync_type == "orders":
        return bulk_sync_orders_to_sales_orders(tenant=tenant, limit=cint(limit))
    else:
        return {"success": False, "error": "Invalid sync_type. Use 'sellers', 'buyers', 'skus', or 'orders'."}


@frappe.whitelist()
def check_erpnext_availability() -> Dict[str, Any]:
    """
    API endpoint to check ERPNext availability and configuration.

    Returns:
        dict: ERPNext availability status
    """
    installed = is_erpnext_installed()

    result = {
        "installed": installed,
        "doctypes": {}
    }

    if installed:
        for erpnext_dt, trade_hub_dt in ERPNEXT_DOCTYPES.items():
            result["doctypes"][erpnext_dt] = check_erpnext_doctype(erpnext_dt)

    return result


@frappe.whitelist()
def enqueue_sync_seller(seller_name: str) -> Dict[str, Any]:
    """
    Enqueue seller sync as a background job.

    Args:
        seller_name: Name of the Seller Profile

    Returns:
        dict: Job status
    """
    frappe.enqueue(
        "tradehub_core.tradehub_core.utils.erpnext_sync.sync_seller_to_supplier",
        seller_name=seller_name,
        force_update=True,
        queue="short",
        timeout=300
    )

    return {
        "success": True,
        "message": _("Sync job enqueued for {0}").format(seller_name)
    }


@frappe.whitelist()
def enqueue_sync_buyer(buyer_name: str) -> Dict[str, Any]:
    """
    Enqueue buyer sync as a background job.

    Args:
        buyer_name: Name of the Buyer Profile

    Returns:
        dict: Job status
    """
    frappe.enqueue(
        "tradehub_core.tradehub_core.utils.erpnext_sync.sync_buyer_to_customer",
        buyer_name=buyer_name,
        force_update=True,
        queue="short",
        timeout=300
    )

    return {
        "success": True,
        "message": _("Sync job enqueued for {0}").format(buyer_name)
    }


@frappe.whitelist()
def unlink_document(doctype: str, docname: str) -> Dict[str, Any]:
    """
    API endpoint to unlink an ERPNext document.

    Args:
        doctype: The Trade Hub DocType
        docname: The document name

    Returns:
        dict: Unlink result
    """
    success = unlink_erpnext_document(doctype, docname)
    return {
        "success": success,
        "message": _("Successfully unlinked") if success else _("Failed to unlink")
    }


@frappe.whitelist()
def sync_sku(sku_product: str, force: bool = False) -> Dict[str, Any]:
    """
    API endpoint to sync a SKU Product to ERPNext Item.

    Args:
        sku_product: Name of the SKU Product
        force: Force update even if recently synced

    Returns:
        dict: Sync result
    """
    try:
        item_code = sync_sku_to_item(sku_product, force_update=cint(force))
        return {
            "success": True,
            "item_code": item_code,
            "message": _("Successfully synced to Item {0}").format(item_code)
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


@frappe.whitelist()
def enqueue_sync_sku(sku_product: str) -> Dict[str, Any]:
    """
    Enqueue SKU Product sync as a background job.

    Args:
        sku_product: Name of the SKU Product

    Returns:
        dict: Job status
    """
    frappe.enqueue(
        "tradehub_core.tradehub_core.utils.erpnext_sync.sync_sku_to_item",
        sku_product=sku_product,
        force_update=True,
        queue="short",
        timeout=300
    )

    return {
        "success": True,
        "message": _("Sync job enqueued for {0}").format(sku_product)
    }


@frappe.whitelist()
def get_sku_sync_status(sku_product: str) -> Dict[str, Any]:
    """
    Get the ERPNext sync status for a SKU Product.

    Args:
        sku_product: Name of the SKU Product

    Returns:
        dict: Sync status including linked Item
    """
    if not frappe.db.exists("SKU Product", sku_product):
        return {"synced": False, "error": "SKU Product not found"}

    sku = frappe.get_doc("SKU Product", sku_product)

    return {
        "synced": bool(sku.get("erpnext_item_code")),
        "erpnext_doctype": "Item",
        "erpnext_name": sku.get("erpnext_item_code"),
        "sku_status": sku.status,
        "sync_enabled": sku.get("sync_with_erpnext"),
        "can_sync": sku.status in ["Active", "Passive"]
    }


@frappe.whitelist()
def bulk_sync_skus(
    tenant: str = None,
    limit: int = 100
) -> Dict[str, Any]:
    """
    API endpoint for bulk SKU Product sync operations.

    Args:
        tenant: Optional tenant filter
        limit: Maximum SKUs to sync

    Returns:
        dict: Sync results
    """
    if not frappe.has_permission("System Manager"):
        frappe.throw(_("Only System Managers can perform bulk sync"), frappe.PermissionError)

    return bulk_sync_skus_to_items(tenant=tenant, limit=cint(limit))


@frappe.whitelist()
def create_item_from_sku(sku_product: str) -> Dict[str, Any]:
    """
    Create an Item document from a SKU Product without syncing.

    This returns the Item data for preview/editing before saving.

    Args:
        sku_product: Name of the SKU Product

    Returns:
        dict: Item document data
    """
    if not is_erpnext_installed():
        return {
            "success": False,
            "error": _("ERPNext is not installed")
        }

    if not frappe.db.exists("SKU Product", sku_product):
        return {
            "success": False,
            "error": _("SKU Product {0} not found").format(sku_product)
        }

    try:
        item = make_item_from_sku(sku_product)
        return {
            "success": True,
            "item": item.as_dict()
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


@frappe.whitelist()
def sync_order(order_name: str, force: bool = False, submit: bool = False) -> Dict[str, Any]:
    """
    API endpoint to sync an order to ERPNext Sales Order.

    Args:
        order_name: Name of the Trade Hub Order
        force: Force update even if recently synced
        submit: Submit the Sales Order after creation

    Returns:
        dict: Sync result
    """
    try:
        sales_order_name = sync_order_to_sales_order(
            order_name,
            force_update=cint(force),
            submit=cint(submit)
        )
        return {
            "success": True,
            "sales_order": sales_order_name,
            "message": _("Successfully synced to Sales Order {0}").format(sales_order_name)
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


@frappe.whitelist()
def enqueue_sync_order(order_name: str, submit: bool = False) -> Dict[str, Any]:
    """
    Enqueue order sync as a background job.

    Args:
        order_name: Name of the Trade Hub Order
        submit: Submit the Sales Order after creation

    Returns:
        dict: Job status
    """
    frappe.enqueue(
        "tradehub_core.tradehub_core.utils.erpnext_sync.sync_order_to_sales_order",
        order_name=order_name,
        force_update=True,
        submit=cint(submit),
        queue="short",
        timeout=300
    )

    return {
        "success": True,
        "message": _("Sync job enqueued for {0}").format(order_name)
    }


@frappe.whitelist()
def get_order_sync_status(order_name: str) -> Dict[str, Any]:
    """
    Get the ERPNext sync status for an order.

    Args:
        order_name: Name of the Trade Hub Order

    Returns:
        dict: Sync status including linked Sales Order
    """
    if not frappe.db.exists("Order", order_name):
        return {"synced": False, "error": "Order not found"}

    order = frappe.get_doc("Order", order_name)

    return {
        "synced": bool(order.get("linked_sales_order")),
        "erpnext_doctype": "Sales Order",
        "erpnext_name": order.get("linked_sales_order"),
        "order_status": order.status,
        "can_sync": order.status in ["Confirmed", "Processing", "Ready to Ship", "Shipped", "Delivered", "Completed"]
    }


@frappe.whitelist()
def bulk_sync_orders(
    tenant: str = None,
    limit: int = 100
) -> Dict[str, Any]:
    """
    API endpoint for bulk order sync operations.

    Args:
        tenant: Optional tenant filter
        limit: Maximum orders to sync

    Returns:
        dict: Sync results
    """
    if not frappe.has_permission("System Manager"):
        frappe.throw(_("Only System Managers can perform bulk sync"), frappe.PermissionError)

    return bulk_sync_orders_to_sales_orders(tenant=tenant, limit=cint(limit))


@frappe.whitelist()
def create_sales_order_from_order(order_name: str) -> Dict[str, Any]:
    """
    Create a Sales Order document from a Trade Hub Order without syncing.

    This returns the Sales Order data for preview/editing before saving.

    Args:
        order_name: Name of the Trade Hub Order

    Returns:
        dict: Sales Order document data
    """
    if not is_erpnext_installed():
        return {
            "success": False,
            "error": _("ERPNext is not installed")
        }

    if not frappe.db.exists("Order", order_name):
        return {
            "success": False,
            "error": _("Order {0} not found").format(order_name)
        }

    try:
        sales_order = make_sales_order(order_name)
        return {
            "success": True,
            "sales_order": sales_order.as_dict()
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


# =============================================================================
# ORDER SYNC WEBHOOK SUPPORT
# =============================================================================


def trigger_order_sync_webhook(
    order_name: str,
    sales_order_name: str,
    event: str = "sync",
    data: Dict[str, Any] = None
) -> bool:
    """
    Trigger webhooks when an order is synced to ERPNext Sales Order.

    This function sends webhook notifications to registered endpoints when
    order sync events occur (create, update, status change).

    Args:
        order_name: Name of the Trade Hub Order
        sales_order_name: Name of the ERPNext Sales Order
        event: Event type (sync, status_change, cancel)
        data: Additional data to include in webhook payload

    Returns:
        bool: True if webhooks were triggered successfully

    Example:
        trigger_order_sync_webhook("ORD-00001", "SAL-ORD-00001", "sync")
    """
    if not frappe.db.exists("Order", order_name):
        return False

    order = frappe.get_doc("Order", order_name)

    # Build webhook payload
    payload = {
        "event": f"order.{event}",
        "timestamp": now_datetime().isoformat(),
        "order": {
            "name": order.name,
            "status": order.status,
            "buyer": order.buyer,
            "tenant": order.tenant,
            "total_amount": flt(order.total_amount),
            "currency": order.currency,
        },
        "sales_order": {
            "name": sales_order_name,
        },
        "data": data or {}
    }

    # Get registered webhook endpoints for this tenant
    webhook_endpoints = _get_order_webhook_endpoints(order.tenant)

    if not webhook_endpoints:
        return True  # No webhooks registered, consider as success

    success = True
    for endpoint in webhook_endpoints:
        try:
            _send_webhook(endpoint, payload)
        except Exception as e:
            frappe.log_error(
                message=f"Webhook failed: {endpoint}\n{str(e)}",
                title=f"Order Webhook Error: {order_name}"
            )
            success = False

    return success


def _get_order_webhook_endpoints(tenant: str) -> List[str]:
    """
    Get registered webhook endpoints for order sync events.

    Args:
        tenant: Tenant to get webhooks for

    Returns:
        list: List of webhook endpoint URLs
    """
    # Check if Webhook DocType exists (Frappe's built-in)
    if not frappe.db.exists("DocType", "Webhook"):
        return []

    # Get webhooks configured for Order doctype
    webhooks = frappe.get_all(
        "Webhook",
        filters={
            "webhook_doctype": "Order",
            "enabled": 1
        },
        fields=["name", "request_url"]
    )

    return [w.request_url for w in webhooks if w.request_url]


def _send_webhook(url: str, payload: Dict[str, Any], timeout: int = 30) -> bool:
    """
    Send a webhook request to the specified URL.

    Args:
        url: Webhook endpoint URL
        payload: JSON payload to send
        timeout: Request timeout in seconds

    Returns:
        bool: True if request was successful
    """
    import requests
    import json

    headers = {
        "Content-Type": "application/json",
        "X-Trade-Hub-Event": payload.get("event", "unknown"),
        "X-Trade-Hub-Signature": _generate_webhook_signature(payload)
    }

    response = requests.post(
        url,
        data=json.dumps(payload),
        headers=headers,
        timeout=timeout
    )

    response.raise_for_status()
    return True


def _generate_webhook_signature(payload: Dict[str, Any]) -> str:
    """
    Generate HMAC signature for webhook payload verification.

    Args:
        payload: Webhook payload

    Returns:
        str: HMAC-SHA256 signature
    """
    import hashlib
    import hmac
    import json

    # Get webhook secret from settings
    secret = frappe.conf.get("webhook_secret", "trade_hub_webhook_secret")

    message = json.dumps(payload, sort_keys=True)
    signature = hmac.new(
        secret.encode(),
        message.encode(),
        hashlib.sha256
    ).hexdigest()

    return f"sha256={signature}"


@frappe.whitelist(allow_guest=True)
def handle_sales_order_webhook() -> Dict[str, Any]:
    """
    Webhook receiver endpoint for ERPNext Sales Order changes.

    This endpoint receives webhook calls from ERPNext when Sales Orders
    are created, updated, or have status changes. It syncs the changes
    back to the linked Trade Hub Order.

    The webhook should be configured in ERPNext to call this endpoint
    when Sales Order events occur.

    Expected webhook payload format:
    {
        "event": "on_update" | "on_submit" | "on_cancel",
        "doctype": "Sales Order",
        "name": "SAL-ORD-00001",
        "data": { ... full document data ... }
    }

    Returns:
        dict: Processing result

    Example:
        # Configure in ERPNext:
        # Webhook URL: https://trade-hub.example.com/api/method/trade_hub.utils.erpnext_sync.handle_sales_order_webhook
        # DocType: Sales Order
        # Events: on_update, on_submit, on_cancel
    """
    import json

    # Verify webhook signature if configured
    signature = frappe.request.headers.get("X-ERPNext-Signature", "")
    if not _verify_webhook_signature(frappe.request.data, signature):
        frappe.throw(_("Invalid webhook signature"), frappe.AuthenticationError)

    try:
        # Parse webhook payload
        payload = json.loads(frappe.request.data)

        event = payload.get("event")
        doctype = payload.get("doctype")
        docname = payload.get("name")
        data = payload.get("data", {})

        # Validate payload
        if doctype != "Sales Order":
            return {
                "success": False,
                "error": f"Unexpected doctype: {doctype}"
            }

        if not docname:
            return {
                "success": False,
                "error": "Missing document name"
            }

        # Process the webhook based on event type
        if event in ["on_update", "on_submit", "on_change"]:
            # Sync Sales Order changes back to Trade Hub Order
            order_name = sync_sales_order_to_order(
                docname,
                ignore_permissions=True
            )

            if order_name:
                return {
                    "success": True,
                    "message": f"Synced Sales Order {docname} to Order {order_name}",
                    "order": order_name
                }
            else:
                return {
                    "success": True,
                    "message": f"No linked Order found for Sales Order {docname}"
                }

        elif event == "on_cancel":
            # Handle Sales Order cancellation
            order_name = _handle_sales_order_cancellation(docname)
            return {
                "success": True,
                "message": f"Handled cancellation for Sales Order {docname}",
                "order": order_name
            }

        else:
            return {
                "success": True,
                "message": f"Unhandled event type: {event}"
            }

    except json.JSONDecodeError:
        return {
            "success": False,
            "error": "Invalid JSON payload"
        }
    except Exception as e:
        frappe.log_error(
            message=str(e),
            title="Sales Order Webhook Error"
        )
        return {
            "success": False,
            "error": str(e)
        }


def _verify_webhook_signature(payload: bytes, signature: str) -> bool:
    """
    Verify the webhook signature from ERPNext.

    Args:
        payload: Raw request body
        signature: Signature from request header

    Returns:
        bool: True if signature is valid or not configured
    """
    import hashlib
    import hmac

    # Get webhook secret from settings
    secret = frappe.conf.get("erpnext_webhook_secret")

    # If no secret configured, skip verification (for development)
    if not secret:
        return True

    if not signature:
        return False

    # Calculate expected signature
    expected = hmac.new(
        secret.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()

    # Compare signatures
    if signature.startswith("sha256="):
        signature = signature[7:]

    return hmac.compare_digest(expected, signature)


def _handle_sales_order_cancellation(sales_order_name: str) -> Optional[str]:
    """
    Handle Sales Order cancellation by updating the linked Trade Hub Order.

    Args:
        sales_order_name: Name of the cancelled Sales Order

    Returns:
        str or None: Name of the updated Order, or None if not found
    """
    # Find linked order
    order_name = frappe.db.get_value(
        "Order",
        {"linked_sales_order": sales_order_name},
        "name"
    )

    if not order_name:
        # Try by PO reference
        sales_order = frappe.get_doc("Sales Order", sales_order_name)
        if sales_order.po_no:
            order_name = frappe.db.get_value(
                "Order",
                {"name": sales_order.po_no},
                "name"
            )

    if not order_name:
        return None

    # Update order status to reflect cancellation
    order = frappe.get_doc("Order", order_name)

    # Only update if order is not already in a final state
    if order.status not in ["Completed", "Cancelled", "Refunded"]:
        order.status = "Cancelled"
        order.add_comment(
            "Info",
            _("Linked Sales Order {0} was cancelled in ERPNext").format(sales_order_name)
        )
        order.save(ignore_permissions=True)

    return order_name


@frappe.whitelist()
def register_order_webhook(
    webhook_url: str,
    events: List[str] = None,
    tenant: str = None
) -> Dict[str, Any]:
    """
    Register a webhook endpoint for order sync events.

    This creates a Frappe Webhook configuration to call the specified URL
    when order events occur.

    Args:
        webhook_url: URL to receive webhook calls
        events: List of events to subscribe to (default: all)
        tenant: Optional tenant filter

    Returns:
        dict: Registration result with webhook name

    Example:
        register_order_webhook(
            "https://external-system.example.com/webhook",
            events=["on_update", "on_submit"]
        )
    """
    if not frappe.has_permission("Webhook", "create"):
        frappe.throw(_("You do not have permission to create webhooks"), frappe.PermissionError)

    if not webhook_url:
        return {
            "success": False,
            "error": "Webhook URL is required"
        }

    # Default events
    if not events:
        events = ["after_insert", "on_update", "on_submit"]

    try:
        # Create webhook for Order doctype
        webhook = frappe.new_doc("Webhook")
        webhook.webhook_doctype = "Order"
        webhook.webhook_docevent = events[0] if len(events) == 1 else "on_update"
        webhook.request_url = webhook_url
        webhook.request_method = "POST"
        webhook.request_structure = "Form URL-Encoded"
        webhook.enabled = 1

        # Add webhook headers
        webhook.append("webhook_headers", {
            "key": "Content-Type",
            "value": "application/json"
        })

        # Add webhook data fields
        for field in ["name", "status", "buyer", "tenant", "total_amount", "currency"]:
            webhook.append("webhook_data", {
                "fieldname": field,
                "key": field
            })

        webhook.insert(ignore_permissions=True)

        return {
            "success": True,
            "webhook": webhook.name,
            "message": _("Webhook registered successfully")
        }

    except Exception as e:
        frappe.log_error(
            message=str(e),
            title="Webhook Registration Error"
        )
        return {
            "success": False,
            "error": str(e)
        }


@frappe.whitelist()
def get_order_webhook_status(order_name: str = None) -> Dict[str, Any]:
    """
    Get webhook configuration status for orders.

    Args:
        order_name: Optional specific order to check

    Returns:
        dict: Webhook status including registered endpoints
    """
    result = {
        "webhooks_enabled": frappe.db.exists("DocType", "Webhook") is not None,
        "registered_webhooks": [],
        "recent_webhook_logs": []
    }

    if result["webhooks_enabled"]:
        # Get registered webhooks for Order
        webhooks = frappe.get_all(
            "Webhook",
            filters={
                "webhook_doctype": "Order"
            },
            fields=["name", "request_url", "enabled", "webhook_docevent"]
        )
        result["registered_webhooks"] = webhooks

        # Get recent webhook logs if available
        if frappe.db.exists("DocType", "Webhook Request Log"):
            logs = frappe.get_all(
                "Webhook Request Log",
                filters={
                    "reference_doctype": "Order"
                },
                fields=["name", "reference_name", "user", "creation", "url"],
                limit_page_length=10,
                order_by="creation desc"
            )
            result["recent_webhook_logs"] = logs

    return result


@frappe.whitelist()
def sync_order_with_webhook(
    order_name: str,
    force: bool = False,
    submit: bool = False,
    trigger_webhook: bool = True
) -> Dict[str, Any]:
    """
    Sync order to Sales Order and optionally trigger webhooks.

    This is a convenience function that combines order sync with
    webhook notification.

    Args:
        order_name: Name of the Trade Hub Order
        force: Force update even if recently synced
        submit: Submit the Sales Order after creation
        trigger_webhook: Whether to trigger webhooks after sync

    Returns:
        dict: Sync result with webhook status

    Example:
        result = sync_order_with_webhook("ORD-00001", trigger_webhook=True)
        if result["success"]:
            print(f"Synced to {result['sales_order']}")
            if result["webhook_triggered"]:
                print("Webhooks notified")
    """
    try:
        # Perform the sync
        sales_order_name = sync_order_to_sales_order(
            order_name,
            force_update=cint(force),
            submit=cint(submit)
        )

        if not sales_order_name:
            return {
                "success": False,
                "error": "Sync failed or not enabled"
            }

        result = {
            "success": True,
            "sales_order": sales_order_name,
            "message": _("Successfully synced to Sales Order {0}").format(sales_order_name),
            "webhook_triggered": False
        }

        # Trigger webhooks if requested
        if trigger_webhook:
            webhook_success = trigger_order_sync_webhook(
                order_name,
                sales_order_name,
                event="sync"
            )
            result["webhook_triggered"] = webhook_success

        return result

    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


# =============================================================================
# MARKETPLACE ORDER -> SALES ORDER SYNC
# =============================================================================


def create_sales_order_from_marketplace_order(marketplace_order: Document) -> str:
    """
    Create an ERPNext Sales Order from a Marketplace Order.
    """
    if not is_erpnext_installed():
        return None

    from frappe.model.mapper import get_mapped_doc

    def set_missing_values(source, target):
        """Set default values and calculate totals."""
        # Get Customer from Buyer Profile
        buyer = frappe.get_doc("Buyer Profile", source.buyer)
        if buyer.get("erpnext_customer"):
            target.customer = buyer.erpnext_customer
        else:
            # Try to sync buyer first if not already synced
            customer_name = sync_buyer_to_customer(source.buyer, ignore_permissions=True)
            if customer_name:
                target.customer = customer_name

        # Set delivery date if available
        if source.estimated_delivery_date:
            target.delivery_date = source.estimated_delivery_date
        else:
            # Default to order_date + delivery_days or 30 days
            target.delivery_date = frappe.utils.add_days(
                source.order_date,
                source.delivery_days or 30
            )

        # Set company (required for Sales Order)
        if not target.company:
            target.company = frappe.defaults.get_user_default("Company") or frappe.db.get_single_value("Global Defaults", "default_company")

        # Calculate totals
        target.run_method("set_missing_values")
        target.run_method("calculate_taxes_and_totals")

    def update_item(source_item, target_item, source_parent):
        """Map item fields and set item_code from SKU Product."""
        # Get Item Code from SKU Product
        if source_item.sku_product:
            sku_product = frappe.get_doc("SKU Product", source_item.sku_product)
            if sku_product.get("erpnext_item"):
                target_item.item_code = sku_product.erpnext_item
            else:
                # Use SKU code as item_code if no ERPNext Item linked
                target_item.item_code = sku_product.sku_code

        # Set delivery date for item
        if source_parent.estimated_delivery_date:
            target_item.delivery_date = source_parent.estimated_delivery_date
        else:
            target_item.delivery_date = frappe.utils.add_days(
                source_parent.order_date,
                source_parent.delivery_days or 30
            )

    doc = get_mapped_doc(
        "Marketplace Order",
        marketplace_order.name,
        {
            "Marketplace Order": {
                "doctype": "Sales Order",
                "field_map": {
                    "order_date": "transaction_date",
                    "currency": "currency",
                    "total_amount": "grand_total",
                    "buyer_notes": "terms",
                    "seller_notes": "notes",
                },
                "validation": {
                    "docstatus": ["=", 0]
                }
            },
            "Order Item": {
                "doctype": "Sales Order Item",
                "field_map": {
                    "quantity": "qty",
                    "unit_price": "rate",
                    "uom": "uom",
                    "item_description": "description",
                    "amount": "amount",
                },
                "postprocess": update_item
            }
        },
        None,
        set_missing_values
    )
    
    doc.insert(ignore_permissions=True)
    return doc.name


def sync_sales_order_from_marketplace_order(marketplace_order: Document) -> bool:
    """
    Sync Marketplace Order changes to linked ERPNext Sales Order.
    """
    if not marketplace_order.erpnext_sales_order:
        return False
        
    try:
        sales_order = frappe.get_doc("Sales Order", marketplace_order.erpnext_sales_order)
        # Update basics
        sales_order.transaction_date = marketplace_order.order_date
        sales_order.notes = marketplace_order.seller_notes
        sales_order.terms = marketplace_order.buyer_notes
        sales_order.save(ignore_permissions=True)
        return True
    except Exception as e:
        frappe.log_error(f"Failed to sync Sales Order: {str(e)}")
        return False


def cancel_sales_order_for_marketplace_order(marketplace_order: Document) -> bool:
    """
    Cancel linked ERPNext Sales Order.
    """
    if not marketplace_order.erpnext_sales_order:
        return True
        
    try:
        sales_order = frappe.get_doc("Sales Order", marketplace_order.erpnext_sales_order)
        if sales_order.docstatus == 1:
            sales_order.cancel()
        return True
    except Exception as e:
        frappe.log_error(f"Failed to cancel Sales Order: {str(e)}")
        return False


def submit_sales_order_for_marketplace_order(marketplace_order: Document) -> bool:
    """
    Submit linked ERPNext Sales Order.
    """
    if not marketplace_order.erpnext_sales_order:
        return False
        
    try:
        sales_order = frappe.get_doc("Sales Order", marketplace_order.erpnext_sales_order)
        if sales_order.docstatus == 0:
            sales_order.submit()
        return True
    except Exception as e:
        frappe.log_error(f"Failed to submit Sales Order: {str(e)}")
        return False
