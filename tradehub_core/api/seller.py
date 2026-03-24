import re
import frappe
from frappe import _


def _strip_html(text):
    """Strip HTML tags from text for plain-text display."""
    if not text:
        return ""
    return re.sub(r'<[^>]+>', '', text).strip()


@frappe.whitelist(allow_guest=True)
def get_sellers(search=None, page=1, page_size=20):
    filters = {"status": "Active"}
    if search:
        filters["seller_name"] = ["like", "%" + search + "%"]
    sellers = frappe.get_all(
        "Admin Seller Profile", filters=filters,
        fields=["name", "seller_code", "seller_name", "city", "country",
                "logo", "banner_image", "description", "slogan",
                "email", "website", "phone", "status",
                "rating", "total_orders", "health_score"],
        limit_start=(int(page)-1)*int(page_size), limit_page_length=int(page_size),
        order_by="seller_name asc"
    )
    for s in sellers:
        s["slug"] = s.get("seller_code") or s.get("name", "")
        s["rating"] = float(s.get("rating") or 0)
        s["review_count"] = int(s.get("total_orders") or 0)
        s["cover_image"] = s.get("banner_image", "")
        s["short_description"] = _strip_html(s.get("description", ""))
        s["verified"] = bool(s.get("health_score", 0) >= 80)
        try:
            seller_code = s.get("seller_code") or s.get("name", "")
            listings = frappe.get_all(
                "Listing",
                filters={"seller_profile": seller_code, "status": "Active"},
                fields=["name", "title", "primary_image", "selling_price", "base_price", "min_order_qty", "b2b_enabled"],
                limit=4
            )
            products = []
            for l in listings:
                price_min = l.selling_price or l.base_price or 0
                price_max = l.base_price or l.selling_price or 0
                if l.get("b2b_enabled"):
                    tiers = frappe.get_all(
                        "Listing Bulk Pricing Tier",
                        filters={"parent": l.name, "parenttype": "Listing"},
                        fields=["price"],
                        order_by="price ASC",
                    )
                    if tiers:
                        price_min = min(t.price for t in tiers)
                        price_max = max(t.price for t in tiers)
                products.append({
                    "name": l.name,
                    "product_name": l.title,
                    "image": l.primary_image,
                    "price_min": price_min,
                    "price_max": price_max,
                    "moq": l.min_order_qty or 1,
                    "moq_unit": "Adet"
                })
            s["products"] = products
            s["product_images"] = [p["image"] for p in products if p.get("image")]
        except Exception:
            s["products"] = []
            s["product_images"] = []
        try:
            gallery = frappe.get_all(
                "Seller Gallery Image",
                filters={"parent": s.get("name"), "parenttype": "Admin Seller Profile"},
                fields=["image"],
                order_by="idx asc",
                limit=20
            )
            s["gallery_images"] = [g["image"] for g in gallery if g.get("image")]
        except Exception:
            s["gallery_images"] = []
    total = frappe.db.count("Admin Seller Profile", filters=filters)
    return {"sellers": sellers, "total": total, "page": int(page), "page_size": int(page_size)}


@frappe.whitelist(allow_guest=True)
def get_seller(slug):
    seller = frappe.db.get_value(
        "Admin Seller Profile", {"seller_code": slug, "status": "Active"},
        ["name", "seller_code", "seller_name", "city", "country",
         "logo", "banner_image", "description", "slogan",
         "email", "phone", "website", "status",
         "rating", "total_orders", "health_score",
         "founded_year", "staff_count", "annual_revenue",
         "factory_size", "business_type", "main_markets"],
        as_dict=True
    )
    if not seller:
        frappe.throw(_("Satici bulunamadi"), frappe.DoesNotExistError)
    seller["slug"] = seller.get("seller_code", "")
    seller["rating"] = float(seller.get("rating") or 0)
    seller["review_count"] = int(seller.get("total_orders") or 0)
    seller["cover_image"] = seller.get("banner_image", "")
    seller["short_description"] = _strip_html(seller.get("description", ""))
    seller["verified"] = bool(seller.get("health_score", 0) >= 80)
    return seller


@frappe.whitelist()
def get_my_supplier_profile():
    """Returns the Admin Seller Profile name for the logged-in seller."""
    user = frappe.session.user
    if not user or user == "Guest":
        return None
    return frappe.db.get_value("Admin Seller Profile", {"user": user}, "name")


@frappe.whitelist()
def get_my_admin_seller_profile():
    """Returns the Admin Seller Profile name (seller_code) for the logged-in seller."""
    user = frappe.session.user
    if not user or user == "Guest":
        return None
    return frappe.db.get_value("Admin Seller Profile", {"user": user}, "name")


@frappe.whitelist(allow_guest=True)
def get_seller_categories(seller_code):
    """Public: Sadece onaylanmış kategorileri döndür."""
    if not frappe.db.exists("Admin Seller Profile", seller_code):
        return {"categories": []}
    cats = frappe.get_all(
        "Seller Category",
        filters={"seller": seller_code, "status": "Active"},
        fields=["name", "category_name", "image", "sort_order"],
        order_by="sort_order asc, category_name asc"
    )
    return {"categories": cats}


@frappe.whitelist()
def add_seller_category(category_name, description="", image="", sort_order=0):
    """Satıcı: yeni kategori ekle (Pending olarak)."""
    seller_profile = _get_seller_profile_for_session()
    if not seller_profile:
        frappe.throw(_("Satıcı profili bulunamadı."))
    doc = frappe.get_doc({
        "doctype": "Seller Category",
        "seller": seller_profile,
        "category_name": category_name,
        "description": description,
        "image": image,
        "sort_order": int(sort_order or 0),
        "status": "Pending",
    })
    doc.insert(ignore_permissions=True)
    return {"success": True, "name": doc.name}


@frappe.whitelist()
def get_my_seller_categories():
    """Satıcı: kendi tüm kategorilerini (tüm statüler) döndür."""
    seller_profile = _get_seller_profile_for_session()
    if not seller_profile:
        return {"success": True, "categories": []}
    cats = frappe.get_all(
        "Seller Category",
        filters={"seller": seller_profile},
        fields=["name", "category_name", "status", "description", "image", "sort_order", "reject_reason"],
        order_by="creation desc",
    )
    return {"success": True, "categories": cats}


@frappe.whitelist()
def delete_seller_category(category_name):
    """Satıcı: kendi kategorisini sil (Pending veya Rejected olabilir)."""
    seller_profile = _get_seller_profile_for_session()
    if not seller_profile:
        frappe.throw(_("Satıcı profili bulunamadı."))
    cat = frappe.get_doc("Seller Category", category_name)
    if cat.seller != seller_profile:
        frappe.throw(_("Bu kategori size ait değil."), frappe.PermissionError)
    cat.delete(ignore_permissions=True)
    return {"success": True}


@frappe.whitelist()
def get_pending_seller_categories(page=1, page_size=20):
    """Admin: Onay bekleyen kategorileri listele."""
    if "System Manager" not in frappe.get_roles() and frappe.session.user != "Administrator":
        frappe.throw(_("Yetki hatası"), frappe.PermissionError)
    page = int(page)
    page_size = int(page_size)
    total = frappe.db.count("Seller Category", {"status": "Pending"})
    cats = frappe.get_all(
        "Seller Category",
        filters={"status": "Pending"},
        fields=["name", "category_name", "seller", "description", "image", "creation"],
        order_by="creation asc",
        start=(page - 1) * page_size,
        page_length=page_size,
    )
    for c in cats:
        if c.get("seller"):
            c["seller_name"] = frappe.db.get_value("Admin Seller Profile", c["seller"], "seller_name") or c["seller"]
        else:
            c["seller_name"] = "-"
    return {"success": True, "categories": cats, "total": total}


@frappe.whitelist()
def approve_seller_category(category_name, action="approve", reject_reason=""):
    """Admin: kategoriyi onayla veya reddet."""
    if "System Manager" not in frappe.get_roles() and frappe.session.user != "Administrator":
        frappe.throw(_("Yetki hatası"), frappe.PermissionError)
    cat = frappe.get_doc("Seller Category", category_name)
    if action == "approve":
        cat.status = "Active"
        cat.reject_reason = ""
    elif action == "reject":
        cat.status = "Rejected"
        cat.reject_reason = reject_reason
    else:
        frappe.throw(_("Geçersiz işlem"))
    cat.save(ignore_permissions=True)
    return {"success": True, "status": cat.status}


def _get_seller_profile_for_session():
    user = frappe.session.user
    profile = frappe.db.get_value("Admin Seller Profile", {"owner": user}, "name")
    if not profile:
        profile = frappe.db.get_value("Admin Seller Profile", {"email": user}, "name")
    return profile


@frappe.whitelist(allow_guest=True)
def get_seller_products(seller_code, category=None, page=1, page_size=40):
    # seller_code = Admin Seller Profile name
    if not frappe.db.exists("Admin Seller Profile", seller_code):
        return {"products": [], "total": 0}
    filters = {"seller_profile": seller_code, "status": "Active"}
    if category:
        filters["category"] = category
    listings = frappe.get_all(
        "Listing",
        filters=filters,
        fields=["name", "title", "primary_image", "selling_price", "base_price",
                "min_order_qty", "category", "short_description", "b2b_enabled"],
        limit_start=(int(page)-1)*int(page_size),
        limit_page_length=int(page_size),
        order_by="creation desc"
    )
    for l in listings:
        l["id"] = l.get("name", "")
        l["product_name"] = l.get("title", "")
        l["image"] = l.get("primary_image", "")
        price_min = l.get("selling_price") or l.get("base_price") or 0
        price_max = l.get("base_price") or l.get("selling_price") or 0
        if l.get("b2b_enabled"):
            tiers = frappe.get_all(
                "Listing Bulk Pricing Tier",
                filters={"parent": l.get("name"), "parenttype": "Listing"},
                fields=["price"],
                order_by="price ASC",
            )
            if tiers:
                price_min = min(t.price for t in tiers)
                price_max = max(t.price for t in tiers)
        l["price_min"] = price_min
        l["price_max"] = price_max
        l["moq"] = l.get("min_order_qty", 1)
        l["moq_unit"] = "Adet"
    total = frappe.db.count("Listing", filters=filters)
    return {"products": listings, "total": total}


@frappe.whitelist(allow_guest=True)
def get_reviews(seller_code, page=1, page_size=10):
    if not frappe.db.exists("Admin Seller Profile", seller_code):
        frappe.throw(_("Satici bulunamadi"), frappe.DoesNotExistError)
    filters = {"seller": seller_code}
    reviews = frappe.get_all(
        "Seller Review", filters=filters,
        fields=["name", "reviewer_name", "rating", "comment", "creation", "product_name"],
        limit_start=(int(page)-1)*int(page_size), limit_page_length=int(page_size),
        order_by="creation desc"
    )
    total = frappe.db.count("Seller Review", filters=filters)
    return {"reviews": reviews, "total": total}


@frappe.whitelist()
def get_gallery():
    """Oturumdaki satıcının galeri fotoğraflarını döndürür."""
    user = frappe.session.user
    profile_name = frappe.db.get_value("Admin Seller Profile", {"user": user}, "name")
    if not profile_name:
        frappe.throw(_("Profil bulunamadi"), frappe.DoesNotExistError)
    doc = frappe.get_doc("Admin Seller Profile", profile_name)
    return [{"name": r.name, "image": r.image, "caption": r.caption or ""} for r in doc.gallery_images]


@frappe.whitelist()
def add_gallery_image(image_url, caption=""):
    """Galerik listesine yeni fotoğraf ekler (max 20)."""
    user = frappe.session.user
    profile_name = frappe.db.get_value("Admin Seller Profile", {"user": user}, "name")
    if not profile_name:
        frappe.throw(_("Profil bulunamadi"))
    doc = frappe.get_doc("Admin Seller Profile", profile_name)
    if len(doc.gallery_images) >= 20:
        frappe.throw(_("Maksimum 20 fotograf yukleyebilirsiniz"))
    doc.append("gallery_images", {"image": image_url, "caption": caption})
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    return [{"name": r.name, "image": r.image, "caption": r.caption or ""} for r in doc.gallery_images]


@frappe.whitelist()
def remove_gallery_image(row_name):
    """Galeriden bir fotoğrafı kaldırır."""
    user = frappe.session.user
    profile_name = frappe.db.get_value("Admin Seller Profile", {"user": user}, "name")
    if not profile_name:
        frappe.throw(_("Profil bulunamadi"))
    doc = frappe.get_doc("Admin Seller Profile", profile_name)
    doc.gallery_images = [r for r in doc.gallery_images if r.name != row_name]
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    return True


@frappe.whitelist(allow_guest=True)
def send_inquiry(seller_code, message, share_business_card=0):
    # Admin Seller Profile'da name = seller_code
    if not frappe.db.exists("Admin Seller Profile", seller_code):
        frappe.throw(_("Satici bulunamadi"), frappe.DoesNotExistError)
    sender_name, sender_email = "", ""
    if frappe.session.user and frappe.session.user != "Guest":
        user = frappe.db.get_value("User", frappe.session.user, ["full_name", "email"], as_dict=True)
        if user:
            sender_name = user.full_name or ""
            sender_email = user.email or ""
    doc = frappe.new_doc("Seller Inquiry")
    doc.seller = seller_code
    doc.seller_code = seller_code
    doc.message = message.strip()
    doc.sender_name = sender_name
    doc.sender_email = sender_email
    doc.share_business_card = int(share_business_card)
    doc.status = "Yeni"
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
    return {"success": True, "inquiry_id": doc.name}
