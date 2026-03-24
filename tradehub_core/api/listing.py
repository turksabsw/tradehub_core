import frappe
from frappe import _
import json
import datetime


@frappe.whitelist(allow_guest=True)
def get_listings(
    query=None,
    category=None,
    min_price=None,
    max_price=None,
    supplier=None,
    sort_by="modified",
    sort_order="DESC",
    page=1,
    page_size=20,
    is_featured=None,
    is_best_seller=None,
    is_new_arrival=None,
    verified_supplier=None,
    min_rating=None,
    country=None,
    free_shipping=None,
):
    """Get paginated list of active listings for the product listing page.

    Returns data matching the frontend ProductListingCard interface.
    """
    page = int(page)
    page_size = min(int(page_size), 100)
    start = (page - 1) * page_size

    filters = {
        "status": "Active",
        "is_visible": 1,
    }

    if category:
        filters["category"] = category
    if is_featured:
        filters["is_featured"] = 1
    if is_best_seller:
        filters["is_best_seller"] = 1
    if is_new_arrival:
        filters["is_new_arrival"] = 1
    if free_shipping:
        filters["is_free_shipping"] = 1

    or_filters = None
    if query:
        or_filters = [
            ["title", "like", f"%{query}%"],
            ["short_description", "like", f"%{query}%"],
            ["brand", "like", f"%{query}%"],
        ]

    if min_price:
        filters["selling_price"] = [">=", float(min_price)]
    if max_price:
        if "selling_price" in filters:
            filters["selling_price"] = ["between", [float(min_price or 0), float(max_price)]]
        else:
            filters["selling_price"] = ["<=", float(max_price)]

    # Valid sort fields
    valid_sort_fields = {
        "modified": "modified",
        "price_asc": "selling_price",
        "price_desc": "selling_price",
        "newest": "creation",
        "rating": "average_rating",
        "orders": "order_count",
        "relevance": "modified",
    }

    actual_sort_field = valid_sort_fields.get(sort_by, "modified")
    if sort_by == "price_asc":
        sort_order = "ASC"
    elif sort_by == "price_desc":
        sort_order = "DESC"
    elif sort_by == "rating":
        sort_order = "DESC"
    elif sort_by == "orders":
        sort_order = "DESC"

    fields = [
        "name", "listing_code", "title", "primary_image",
        "selling_price", "base_price", "compare_at_price", "currency",
        "discount_percentage", "min_order_qty", "stock_uom",
        "order_count", "average_rating", "review_count",
        "seller_profile", "supplier_display_name",
        "ships_from_country", "country_of_origin",
        "is_free_shipping", "is_featured", "is_best_seller",
        "is_new_arrival", "is_on_sale", "selling_point",
        "b2b_enabled", "has_variants", "category", "category_name",
        "brand", "modified", "creation",
    ]

    listings = frappe.get_all(
        "Listing",
        filters=filters,
        or_filters=or_filters,
        fields=fields,
        order_by=f"{actual_sort_field} {sort_order}",
        start=start,
        page_length=page_size,
    )

    total = frappe.db.count("Listing", filters=filters)

    # Enrich listings with supplier info and pricing tiers
    results = []
    for listing in listings:
        item = _format_listing_card(listing)
        results.append(item)

    return {
        "data": results,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, -(-total // page_size)),  # ceil division
        "has_next": (start + page_size) < total,
        "has_prev": page > 1,
    }


@frappe.whitelist(allow_guest=True)
def get_listing_detail(listing_id):
    """Get full listing detail for the product detail page.

    Returns data matching the frontend ProductDetail interface.
    """
    if not listing_id:
        frappe.throw(_("Listing ID is required"))

    # Try to find by name or listing_code
    listing_name = listing_id
    if not frappe.db.exists("Listing", listing_id):
        listings = frappe.get_all("Listing", filters={"listing_code": listing_id}, limit=1)
        if listings:
            listing_name = listings[0].name
        else:
            frappe.throw(_("Listing not found"), frappe.DoesNotExistError)

    listing = frappe.get_doc("Listing", listing_name)

    # Increment view count
    frappe.db.set_value("Listing", listing_name, "view_count", (listing.view_count or 0) + 1, update_modified=False)

    # Get supplier info from Admin Seller Profile
    supplier_data = None
    if listing.seller_profile:
        try:
            seller = frappe.get_doc("Admin Seller Profile", listing.seller_profile)
            years_in_business = 0
            if seller.founded_year:
                try:
                    years_in_business = datetime.datetime.now().year - int(seller.founded_year)
                except (ValueError, TypeError):
                    years_in_business = 0
            supplier_data = {
                "name": seller.seller_name or seller.company_name,
                "companyName": seller.company_name,
                "verified": bool(seller.is_verified),
                "verificationType": seller.verification_type,
                "yearsInBusiness": years_in_business,
                "country": seller.country,
                "city": seller.city,
                "logo": seller.logo,
                "responseTime": seller.response_time,
                "responseRate": seller.response_rate or 0,
                "onTimeDelivery": seller.on_time_delivery or 0,
                "mainProducts": [p.strip() for p in (seller.main_markets or "").split(",") if p.strip()],
                "employees": seller.staff_count,
                "annualRevenue": seller.annual_revenue,
                "certifications": [c.strip() for c in (seller.certifications or "").split(",") if c.strip()],
                "rating": seller.rating or 0,
                "reviewCount": seller.review_count or 0,
            }
        except Exception:
            pass

    # Build category breadcrumb
    category_breadcrumb = _get_category_breadcrumb(listing.category)

    # Get images — primary_image + listing_images child table
    images = [listing.primary_image] if listing.primary_image else []
    for img in (listing.listing_images or []):
        if img.image:
            images.append(img.image)

    # Fallback: if no images found, check Frappe sidebar attachments (File doctype)
    if not images:
        attachments = frappe.get_all(
            "File",
            filters={
                "attached_to_doctype": "Listing",
                "attached_to_name": listing_name,
                "is_private": 0,
            },
            fields=["file_url"],
            order_by="creation asc",
        )
        image_exts = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".svg")
        for f in attachments:
            url = f.get("file_url", "")
            if url and any(url.lower().endswith(ext) for ext in image_exts):
                images.append(url)

    # Get pricing tiers
    price_tiers = []
    if listing.b2b_enabled and listing.pricing_tiers:
        for tier in listing.pricing_tiers:
            price_tiers.append({
                "minQty": tier.min_qty,
                "maxQty": tier.max_qty or None,
                "price": tier.price,
                "currency": listing.currency,
            })

    if not price_tiers:
        price_tiers = [{
            "minQty": listing.min_order_qty or 1,
            "maxQty": None,
            "price": listing.selling_price,
            "currency": listing.currency,
        }]

    # Get variants
    variants = _get_listing_variants(listing_name)

    # Get specifications
    specs = []
    for attr in (listing.attribute_values or []):
        specs.append({
            "label": attr.attribute_name,
            "value": attr.attribute_value,
            "group": attr.attribute_group,
        })

    # Build packaging specs from dedicated fields
    packaging_specs = []
    if listing.package_type:
        packaging_specs.append({"label": "Paket Tipi", "value": listing.package_type})
    if listing.package_length and listing.package_width and listing.package_height:
        packaging_specs.append({"label": "Paket Boyutu", "value": f"{listing.package_length} x {listing.package_width} x {listing.package_height} cm"})
    if listing.package_weight:
        packaging_specs.append({"label": "Paket Ağırlığı", "value": f"{listing.package_weight} kg"})
    if listing.units_per_package:
        packaging_specs.append({"label": "Koli Başına Adet", "value": str(listing.units_per_package)})
    if listing.carton_length and listing.carton_width and listing.carton_height:
        packaging_specs.append({"label": "Koli Boyutu", "value": f"{listing.carton_length} x {listing.carton_width} x {listing.carton_height} cm"})
    if listing.carton_gross_weight:
        packaging_specs.append({"label": "Koli Brüt Ağırlığı", "value": f"{listing.carton_gross_weight} kg"})

    # Get shipping methods
    shipping = []
    for sm in (listing.shipping_methods or []):
        shipping.append({
            "method": sm.shipping_method_name or sm.shipping_method,
            "estimatedDays": f"{sm.min_days}-{sm.max_days}" if sm.min_days and sm.max_days else "",
            "cost": sm.cost,
            "currency": listing.currency,
        })

    # Get customization options
    customization_opts = []
    for opt in (listing.customization_options or []):
        customization_opts.append({
            "name": opt.option_name,
            "description": opt.description,
            "additionalCost": opt.additional_cost,
            "minQty": opt.min_qty,
        })

    # Build price display
    price_range = _get_price_range(listing)

    result = {
        "id": listing.name,
        "listingCode": listing.listing_code,
        "title": listing.title,
        "category": category_breadcrumb,
        "images": images,
        "priceTiers": price_tiers,
        "moq": listing.min_order_qty or 1,
        "unit": listing.stock_uom or "piece",
        "samplePrice": listing.sample_price,
        "currency": listing.currency,
        "sellingPrice": listing.selling_price,
        "basePrice": listing.base_price,
        "compareAtPrice": listing.compare_at_price,
        "discountPercentage": listing.discount_percentage,
        "priceRange": price_range,
        "shipping": shipping,
        "leadTime": f"{listing.handling_days or 1} iş günü" if listing.handling_days else "",
        "leadTimeRanges": [
            {
                "quantityRange": f"{r.min_qty}-{r.max_qty}" if r.max_qty else f"{r.min_qty}+",
                "days": f"{r.lead_days} gün",
            }
            for r in (listing.lead_time_ranges or [])
        ],
        "variants": variants,
        "specs": specs,
        "packagingSpecs": packaging_specs,
        "description": listing.description,
        "shortDescription": listing.short_description,
        "rating": listing.average_rating or 0,
        "reviewCount": listing.review_count or 0,
        "orderCount": listing.order_count or 0,
        "viewCount": listing.view_count or 0,
        "supplier": supplier_data,
        "customizationOptions": customization_opts,
        "brand": listing.brand,
        "condition": listing.condition,
        "isFreeShipping": bool(listing.is_free_shipping),
        "shipsFromCountry": listing.ships_from_country,
        "shipsFromCity": listing.ships_from_city,
        "countryOfOrigin": listing.country_of_origin,
        "isFeatured": bool(listing.is_featured),
        "isBestSeller": bool(listing.is_best_seller),
        "isNewArrival": bool(listing.is_new_arrival),
        "isOnSale": bool(listing.is_on_sale),
        "sellingPoint": listing.selling_point,
        "hasVariants": bool(listing.has_variants),
        "stockQty": listing.available_qty or listing.stock_qty,
        "inStock": (listing.available_qty or listing.stock_qty or 0) > 0,
        "videoUrl": listing.video_url,
    }

    return {"data": result}


@frappe.whitelist(allow_guest=True)
def get_categories(parent=None, include_children=True):
    """Get product categories, optionally filtered by parent.

    Returns hierarchical category structure.
    """
    filters = {"is_active": 1}
    if parent:
        filters["parent_product_category"] = parent
    else:
        filters["parent_product_category"] = ["is", "not set"]

    categories = frappe.get_all(
        "Product Category",
        filters=filters,
        fields=["name", "category_name", "parent_product_category", "image", "icon_class", "url_slug"],
        order_by="category_name ASC",
    )

    results = []
    for cat in categories:
        item = {
            "id": cat.name,
            "name": cat.category_name,
            "slug": cat.url_slug,
            "image": cat.image,
            "icon": cat.icon_class,
            "parent": cat.parent_product_category,
            "children": [],
            "productCount": frappe.db.count("Listing", {"category": cat.name, "status": "Active", "is_visible": 1}),
        }

        if include_children:
            child_cats = frappe.get_all(
                "Product Category",
                filters={"parent_product_category": cat.name, "is_active": 1},
                fields=["name", "category_name", "url_slug", "image"],
                order_by="category_name ASC",
            )
            for child in child_cats:
                item["children"].append({
                    "id": child.name,
                    "name": child.category_name,
                    "slug": child.url_slug,
                    "image": child.image,
                    "productCount": frappe.db.count("Listing", {"category": child.name, "status": "Active", "is_visible": 1}),
                })

        results.append(item)

    return {"data": results}


@frappe.whitelist(allow_guest=True)
def get_shipping_methods(listing_id=None):
    """Get available shipping methods, optionally for a specific listing."""

    if listing_id:
        # Get listing-specific shipping methods
        listing = frappe.get_doc("Listing", listing_id)
        shipping = []
        for sm in (listing.shipping_methods or []):
            shipping.append({
                "id": sm.shipping_method,
                "method": sm.shipping_method_name or sm.shipping_method,
                "cost": sm.cost,
                "minDays": sm.min_days,
                "maxDays": sm.max_days,
                "estimatedDays": f"{sm.min_days}-{sm.max_days} iş günü" if sm.min_days and sm.max_days else "",
                "currency": listing.currency,
            })
        return {"data": shipping}

    # Get all active shipping methods
    methods = frappe.get_all(
        "Shipping Method",
        filters={"is_active": 1},
        fields=["name", "method_name", "shipping_type", "min_days", "max_days", "base_cost", "cost_per_kg", "currency", "description"],
        order_by="base_cost ASC",
    )

    results = []
    for m in methods:
        results.append({
            "id": m.name,
            "method": m.method_name,
            "type": m.shipping_type,
            "minDays": m.min_days,
            "maxDays": m.max_days,
            "estimatedDays": f"{m.min_days}-{m.max_days} iş günü" if m.min_days and m.max_days else "",
            "baseCost": m.base_cost,
            "costPerKg": m.cost_per_kg,
            "currency": m.currency,
            "description": m.description,
        })

    return {"data": results}


@frappe.whitelist(allow_guest=True)
def get_featured_listings(limit=10):
    """Get featured listings for homepage."""
    return get_listings(is_featured=1, page_size=limit)


@frappe.whitelist(allow_guest=True)
def get_related_listings(listing_id, limit=8):
    """Get related listings based on category."""
    if not listing_id:
        return {"data": []}

    listing = frappe.db.get_value("Listing", listing_id, ["category", "brand"], as_dict=True)
    if not listing:
        return {"data": []}

    filters = {
        "status": "Active",
        "is_visible": 1,
        "name": ["!=", listing_id],
    }

    if listing.category:
        filters["category"] = listing.category

    listings = frappe.get_all(
        "Listing",
        filters=filters,
        fields=[
            "name", "listing_code", "title", "primary_image",
            "selling_price", "base_price", "compare_at_price", "currency",
            "discount_percentage", "min_order_qty", "stock_uom",
            "order_count", "average_rating", "review_count",
            "seller_profile", "supplier_display_name",
            "ships_from_country", "country_of_origin",
            "is_free_shipping", "selling_point",
            "b2b_enabled", "category_name", "brand",
        ],
        order_by="order_count DESC",
        limit=int(limit),
    )

    results = [_format_listing_card(l) for l in listings]
    return {"data": results}


@frappe.whitelist(allow_guest=True)
def get_search_suggestions(limit=6):
    """Get search suggestions and category chips for the search bar."""
    limit = int(limit)

    # Top product titles from most ordered/viewed active listings
    top_listings = frappe.get_all(
        "Listing",
        filters={"status": "Active", "is_visible": 1},
        fields=["title"],
        order_by="order_count DESC, view_count DESC",
        limit=limit,
    )

    suggestions = [{"text": _truncate_words(l.title, 5), "type": "product"} for l in top_listings]

    # Top 3 categories by active listing count
    categories = frappe.get_all(
        "Product Category",
        filters={"is_active": 1},
        fields=["name", "category_name"],
    )

    category_counts = []
    for cat in categories:
        count = frappe.db.count("Listing", {"category": cat.name, "status": "Active", "is_visible": 1})
        category_counts.append({"name": cat.category_name, "count": count})

    category_counts.sort(key=lambda x: x["count"], reverse=True)
    chips = [{"text": c["name"], "type": "category"} for c in category_counts[:3]]

    return {
        "data": {
            "suggestions": suggestions,
            "chips": chips,
        }
    }


# ---- Helper Functions ----

def _format_listing_card(listing):
    """Format a listing record into the ProductListingCard structure for frontend."""
    # Get supplier info
    supplier_years = 0
    supplier_country = ""
    supplier_verified = False
    supplier_rating = 0
    supplier_review_count = 0

    if listing.get("seller_profile"):
        try:
            sp = frappe.db.get_value(
                "Admin Seller Profile",
                listing.get("seller_profile"),
                ["founded_year", "country", "is_verified", "rating", "review_count"],
                as_dict=True,
            )
            if sp:
                if sp.founded_year:
                    try:
                        supplier_years = datetime.datetime.now().year - int(sp.founded_year)
                    except (ValueError, TypeError):
                        supplier_years = 0
                supplier_country = _get_country_code(sp.country) if sp.country else ""
                supplier_verified = bool(sp.is_verified)
                supplier_rating = sp.rating or 0
                supplier_review_count = sp.review_count or 0
        except Exception:
            pass

    # Get price range from pricing tiers
    selling_price = listing.get("selling_price") or 0
    min_price_val = selling_price
    max_price_val = selling_price
    price_display = _format_price(selling_price, listing.get("currency"))

    if listing.get("b2b_enabled"):
        tiers = frappe.get_all(
            "Listing Bulk Pricing Tier",
            filters={"parent": listing.name, "parenttype": "Listing"},
            fields=["min_qty", "max_qty", "price"],
            order_by="price ASC",
        )
        if tiers:
            min_price_val = min(t.price for t in tiers)
            max_price_val = max(t.price for t in tiers)
            if min_price_val != max_price_val:
                price_display = f"${min_price_val:.2f}-{max_price_val:.2f}"
            else:
                price_display = f"${min_price_val:.2f}"

    # Get first image
    primary_image = listing.get("primary_image", "")

    return {
        "id": listing.name,
        "listingCode": listing.get("listing_code", ""),
        "name": listing.title,
        "href": f"/pages/product-detail.html?id={listing.name}",
        "price": price_display,
        "sellingPrice": selling_price,
        "minPrice": min_price_val,
        "maxPrice": max_price_val,
        "originalPrice": _format_price(listing.get("compare_at_price"), listing.get("currency")) if listing.get("compare_at_price") else None,
        "discount": f"%{int(listing.get('discount_percentage', 0))} indirim" if listing.get("discount_percentage") else None,
        "moq": f"{listing.get('min_order_qty', 1)} {listing.get('stock_uom', 'Adet')}",
        "stats": f"{_format_number(listing.get('order_count', 0))} satış" if listing.get("order_count") else None,
        "imageSrc": primary_image,
        "images": [],
        "supplierName": listing.get("supplier_display_name", ""),
        "verified": supplier_verified,
        "supplierYears": supplier_years,
        "supplierCountry": supplier_country,
        "rating": listing.get("average_rating", 0),
        "reviewCount": listing.get("review_count", 0),
        "supplierRating": supplier_rating,
        "supplierReviewCount": supplier_review_count,
        "sellingPoint": listing.get("selling_point", ""),
        "promo": listing.get("selling_point", ""),
        "isFreeShipping": bool(listing.get("is_free_shipping")),
        "isFeatured": bool(listing.get("is_featured")),
        "isBestSeller": bool(listing.get("is_best_seller")),
        "isNewArrival": bool(listing.get("is_new_arrival")),
        "category": listing.get("category_name", ""),
        "brand": listing.get("brand", ""),
        "baseCurrency": listing.get("currency", "USD"),
    }


def _get_listing_variants(listing_name):
    """Get variants grouped by attribute name for the product detail page.

    Sources (checked in order):
    1. Listing Variant Item child table (inline in Listing form)
    2. Listing Variant separate DocType (legacy)
    """
    # First check inline variant items (child table)
    inline_variants = frappe.get_all(
        "Listing Variant Item",
        filters={"parent": listing_name, "parenttype": "Listing"},
        fields=["attribute_type", "attribute_value", "variant_image", "variant_price", "variant_stock", "variant_sku"],
        order_by="idx ASC",
    )

    if inline_variants:
        return _build_variants_from_inline(listing_name, inline_variants)

    # Fallback to separate Listing Variant DocType
    variants = frappe.get_all(
        "Listing Variant",
        filters={"listing": listing_name},
        fields=["name", "variant_name", "sku", "price", "stock_qty", "is_active", "primary_image"],
        order_by="variant_name ASC",
    )

    if not variants:
        return []

    # Get listing's base price and stock for fallback
    listing_data = frappe.db.get_value(
        "Listing", listing_name,
        ["selling_price", "stock_qty", "track_inventory"],
        as_dict=True,
    )
    base_price = listing_data.selling_price if listing_data else 0
    listing_stock = listing_data.stock_qty if listing_data else 0
    track_inventory = listing_data.track_inventory if listing_data else 0

    # Group variants by attribute
    variant_groups = {}
    for v in variants:
        attrs = frappe.get_all(
            "Listing Variant Attribute",
            filters={"parent": v.name, "parenttype": "Listing Variant"},
            fields=["attribute_name", "attribute_value"],
            order_by="idx ASC",
        )
        for attr in attrs:
            group_name = attr.attribute_name
            if group_name not in variant_groups:
                variant_groups[group_name] = {
                    "name": group_name,
                    "type": "button",
                    "options": [],
                    "_seen_values": set(),
                }

            if attr.attribute_value not in variant_groups[group_name]["_seen_values"]:
                variant_groups[group_name]["_seen_values"].add(attr.attribute_value)

                # Determine availability:
                # - If track_inventory is off, always available
                # - If variant has stock > 0, available
                # - If variant stock is 0 but listing has stock, available (shared stock)
                if not track_inventory:
                    is_available = True
                elif (v.stock_qty or 0) > 0:
                    is_available = True
                elif (listing_stock or 0) > 0:
                    is_available = True
                else:
                    is_available = False

                # Use variant price, fallback to listing base price
                variant_price = v.price if v.price and v.price > 0 else base_price

                # For legacy DocType, addon is the difference from base
                price_addon = (variant_price - base_price) if variant_price > base_price else 0

                option = {
                    "label": attr.attribute_value,
                    "value": attr.attribute_value,
                    "available": is_available,
                    "image": v.primary_image if v.primary_image else None,
                    "price": variant_price,
                    "priceAddon": price_addon,
                    "stockQty": v.stock_qty or 0,
                    "variantId": v.name,
                }
                variant_groups[group_name]["options"].append(option)

    # Clean up and return
    result = []
    for group in variant_groups.values():
        del group["_seen_values"]
        # If any option has an image, mark type as "image"
        if any(opt.get("image") for opt in group["options"]):
            group["type"] = "image"
        result.append(group)

    return result


def _build_variants_from_inline(listing_name, inline_variants):
    """Build variant groups from Listing Variant Item child table rows."""
    listing_data = frappe.db.get_value(
        "Listing", listing_name,
        ["selling_price", "stock_qty", "track_inventory"],
        as_dict=True,
    )
    base_price = listing_data.selling_price if listing_data else 0
    listing_stock = listing_data.stock_qty if listing_data else 0
    track_inventory = listing_data.track_inventory if listing_data else 0

    variant_groups = {}
    for v in inline_variants:
        group_name = v.attribute_type or "Diğer"
        if group_name not in variant_groups:
            variant_groups[group_name] = {
                "name": group_name,
                "type": "button",
                "options": [],
                "_seen": set(),
            }

        if v.attribute_value and v.attribute_value not in variant_groups[group_name]["_seen"]:
            variant_groups[group_name]["_seen"].add(v.attribute_value)

            # Availability
            if not track_inventory:
                is_available = True
            elif (v.variant_stock or 0) > 0:
                is_available = True
            elif (listing_stock or 0) > 0:
                is_available = True
            else:
                is_available = False

            variant_price = v.variant_price if v.variant_price and v.variant_price > 0 else base_price

            option = {
                "label": v.attribute_value,
                "value": v.attribute_value,
                "available": is_available,
                "image": v.variant_image if v.variant_image else None,
                "price": variant_price,
                "priceAddon": v.variant_price if v.variant_price and v.variant_price > 0 else 0,
                "stockQty": v.variant_stock or 0,
                "variantId": f"{listing_name}-{v.attribute_type}-{v.attribute_value}",
            }
            variant_groups[group_name]["options"].append(option)

    result = []
    for group in variant_groups.values():
        del group["_seen"]
        if any(opt.get("image") for opt in group["options"]):
            group["type"] = "image"
        result.append(group)

    return result


def _get_category_breadcrumb(category_name):
    """Build category breadcrumb path."""
    if not category_name:
        return []

    breadcrumb = []
    current = category_name
    max_depth = 10  # prevent infinite loops

    while current and max_depth > 0:
        cat = frappe.db.get_value(
            "Product Category",
            current,
            ["category_name", "parent_product_category"],
            as_dict=True,
        )
        if cat:
            breadcrumb.insert(0, cat.category_name)
            current = cat.parent_product_category
        else:
            break
        max_depth -= 1

    return breadcrumb


def _get_country_code(country_name):
    """Get 2-letter country code from country name."""
    if not country_name:
        return ""

    country_map = {
        "Turkey": "TR",
        "China": "CN",
        "United States": "US",
        "Germany": "DE",
        "United Kingdom": "GB",
        "Japan": "JP",
        "South Korea": "KR",
        "India": "IN",
        "Italy": "IT",
        "France": "FR",
        "Spain": "ES",
        "Brazil": "BR",
        "Canada": "CA",
        "Australia": "AU",
        "Russia": "RU",
        "Netherlands": "NL",
        "Belgium": "BE",
        "Poland": "PL",
        "Thailand": "TH",
        "Vietnam": "VN",
        "Indonesia": "ID",
        "Malaysia": "MY",
        "Taiwan": "TW",
        "Hong Kong": "HK",
        "Singapore": "SG",
        "Pakistan": "PK",
        "Bangladesh": "BD",
        "Mexico": "MX",
        "Egypt": "EG",
        "Saudi Arabia": "SA",
        "United Arab Emirates": "AE",
    }

    return country_map.get(country_name, country_name[:2].upper() if country_name else "")


def _format_price(price, currency="USD"):
    """Format price for display."""
    if not price:
        return ""

    symbols = {"USD": "$", "EUR": "€", "TRY": "₺", "GBP": "£", "CNY": "¥", "JPY": "¥"}
    symbol = symbols.get(currency, currency + " ")

    return f"{symbol}{price:.2f}"


def _truncate_words(text, max_words=5):
    """Truncate text to max_words words."""
    if not text:
        return ""
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]) + "..."

def _format_number(num):
    """Format number for display (e.g., 19070 -> 19.070)."""
    if not num:
        return "0"
    if num >= 1000:
        return f"{num:,.0f}".replace(",", ".")
    return str(num)


def _get_price_range(listing):
    """Get price range string from listing pricing tiers."""
    if listing.b2b_enabled and listing.pricing_tiers:
        prices = [t.price for t in listing.pricing_tiers if t.price]
        if prices:
            min_p = min(prices)
            max_p = max(prices)
            if min_p != max_p:
                return f"${min_p:.2f}-${max_p:.2f}"
            return f"${min_p:.2f}"

    return _format_price(listing.selling_price, listing.currency)


# ─── Moderasyon Endpoint'leri ──────────────────────────

@frappe.whitelist()
def get_pending_listings(page=1, page_size=20):
    """Admin: Onay bekleyen listing'leri listele."""
    if "System Manager" not in frappe.get_roles() and frappe.session.user != "Administrator":
        frappe.throw(_("Yetki hatası"), frappe.PermissionError)

    page = int(page)
    page_size = int(page_size)
    total = frappe.db.count("Listing", {"status": "Pending"})
    listings = frappe.get_all(
        "Listing",
        filters={"status": "Pending"},
        fields=["name", "title", "status", "seller_profile", "creation", "modified",
                "selling_price", "currency", "stock_qty", "listing_code"],
        order_by="creation asc",
        start=(page - 1) * page_size,
        page_length=page_size,
    )
    for l in listings:
        if l.get("seller_profile"):
            l["seller_name"] = frappe.db.get_value(
                "Admin Seller Profile", l["seller_profile"], "seller_name"
            ) or l["seller_profile"]
        else:
            l["seller_name"] = "-"
    return {"success": True, "listings": listings, "total": total}


@frappe.whitelist()
def approve_listing(listing_name, action="approve", reject_reason=""):
    """Admin: listing'i onayla (Active) veya reddet (Rejected)."""
    if "System Manager" not in frappe.get_roles() and frappe.session.user != "Administrator":
        frappe.throw(_("Yetki hatası"), frappe.PermissionError)

    listing = frappe.get_doc("Listing", listing_name)
    if action == "approve":
        listing.status = "Active"
        listing.flags.ignore_validate = False
    elif action == "reject":
        listing.status = "Rejected"
        if reject_reason:
            listing.add_comment("Comment", text=f"Ret sebebi: {reject_reason}")
    else:
        frappe.throw(_("Geçersiz işlem"))

    listing.flags.from_admin = True
    listing.save(ignore_permissions=True)
    return {"success": True, "status": listing.status}


@frappe.whitelist()
def get_seller_listings(page=1, page_size=20):
    """Satıcı: kendi listing'lerini listele (tüm durumlar)."""
    seller_profile = frappe.db.get_value(
        "Admin Seller Profile", {"owner": frappe.session.user}, "name"
    ) or frappe.db.get_value(
        "Admin Seller Profile", {"email": frappe.session.user}, "name"
    )
    if not seller_profile:
        return {"success": True, "listings": [], "total": 0}

    page = int(page)
    page_size = int(page_size)
    total = frappe.db.count("Listing", {"seller_profile": seller_profile})
    listings = frappe.get_all(
        "Listing",
        filters={"seller_profile": seller_profile},
        fields=["name", "title", "status", "selling_price", "currency",
                "stock_qty", "available_qty", "creation", "listing_code"],
        order_by="creation desc",
        start=(page - 1) * page_size,
        page_length=page_size,
    )
    return {"success": True, "listings": listings, "total": total}


@frappe.whitelist()
def update_listing_status(listing_name, status):
    """Satıcı: onaylanan listing'in durumunu değiştir."""
    allowed = {"Active", "Paused", "Out of Stock"}
    if status not in allowed:
        frappe.throw(_("Geçersiz durum"))

    listing = frappe.get_doc("Listing", listing_name)
    if listing.status in ("Pending", "Rejected", "Draft"):
        frappe.throw(_("Bu listing henüz onaylanmamış."))

    # Sahiplik kontrolü
    seller_profile = frappe.db.get_value(
        "Admin Seller Profile", {"owner": frappe.session.user}, "name"
    ) or frappe.db.get_value(
        "Admin Seller Profile", {"email": frappe.session.user}, "name"
    )
    if listing.seller_profile != seller_profile:
        frappe.throw(_("Bu listing size ait değil."), frappe.PermissionError)

    frappe.db.set_value("Listing", listing_name, "status", status)
    return {"success": True}


@frappe.whitelist()
def get_listing_meta():
    """Satıcı için Listing doctype meta verilerini döndür (field tanımları)."""
    from frappe.model.meta import get_meta
    meta = get_meta("Listing")
    # Sadece UI'da gösterilecek alanları filtrele
    skip_types = {"Section Break", "Tab Break", "Column Break", "HTML", "Button"}
    skip_fields = {"listing_code", "seller_profile", "supplier_display_name",
                   "status", "reserved_qty", "available_qty", "published_at",
                   "erpnext_item", "naming_series", "variants_html",
                   "view_count", "wishlist_count", "order_count",
                   "average_rating", "review_count"}
    fields = []
    for f in meta.fields:
        if f.fieldtype in skip_types:
            continue
        if f.fieldname in skip_fields:
            continue
        if f.read_only:
            continue
        fields.append({
            "fieldname": f.fieldname,
            "fieldtype": f.fieldtype,
            "label": f.label,
            "reqd": f.reqd,
            "options": f.options,
            "default": f.default,
            "depends_on": f.depends_on,
            "description": f.description,
        })
    return {"success": True, "fields": fields}
