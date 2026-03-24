import frappe
from frappe import _
from frappe.utils import random_string
import json


# ─── Yardımcı ────────────────────────────────────────────────────────────────

def _create_seller_profile(user_email: str, seller_name: str, phone: str = None,
                            seller_type: str = "Individual", company_name: str = None,
                            tax_id: str = None, city: str = None, **kwargs) -> dict:
    """Seller Profile oluşturur. Zaten varsa hata fırlatır."""
    if frappe.db.exists("Seller Profile", {"user": user_email}):
        frappe.throw(_("Bu kullanıcının zaten bir satıcı profili var"), frappe.DuplicateEntryError)

    user = frappe.get_doc("User", user_email)

    doc = frappe.new_doc("Seller Profile")
    doc.seller_name = seller_name or user.full_name
    doc.user = user_email
    doc.email = user_email
    doc.phone = phone or ""
    doc.seller_type = seller_type
    doc.company_name = company_name or ""
    doc.tax_id = tax_id or ""
    doc.city = city or ""
    doc.status = "Active"  # Admin onayı gerekmez, direkt aktif
    doc.insert(ignore_permissions=True)

    return {
        "seller_code": doc.seller_code,
        "seller_name": doc.seller_name,
        "status": doc.status,
    }


# ─── Public Endpoint'ler ──────────────────────────────────────────────────────

@frappe.whitelist(allow_guest=True)
def get_current_user():
    """
    Oturumdaki kullanıcı + seller bilgisi döndürür.
    Auth geliştirici login sonrası bu endpoint'i çağırarak seller durumunu öğrenir.

    GET /api/method/tradehub_core.api.auth.get_current_user
    Response:
      { is_guest: bool, email?, full_name?, is_seller: bool, seller?: {...} }
    """
    user_email = frappe.session.user
    if user_email == "Guest":
        return {"is_guest": True}

    user = frappe.get_doc("User", user_email)

    seller = frappe.db.get_value(
        "Seller Profile",
        {"user": user_email},
        ["name", "seller_name", "seller_code", "status", "logo",
         "health_score", "score_grade"],
        as_dict=True,
    )

    return {
        "is_guest": False,
        "email": user.email,
        "full_name": user.full_name,
        "is_seller": bool(seller),
        "seller": seller or None,
    }


@frappe.whitelist(allow_guest=True)
def register(full_name, email, password, phone=None,
             become_seller=False, seller_data=None):
    """
    Yeni kullanıcı kaydı. Opsiyonel olarak seller profili de oluşturur.

    POST /api/method/tradehub_core.api.auth.register
    Body:
      {
        full_name, email, password, phone?,
        become_seller?: bool,
        seller_data?: "{seller_name, seller_type, company_name, tax_id, city}"
      }
    """
    # E-posta kontrolü
    if frappe.db.exists("User", {"email": email}):
        frappe.throw(_("Bu e-posta adresi zaten kayıtlı"))

    # become_seller string gelebilir
    if isinstance(become_seller, str):
        become_seller = become_seller.lower() in ("true", "1", "yes")

    # Kullanıcı oluştur
    name_parts = full_name.strip().split(" ", 1)
    user = frappe.new_doc("User")
    user.email = email
    user.first_name = name_parts[0]
    user.last_name = name_parts[1] if len(name_parts) > 1 else ""
    user.send_welcome_email = 0
    user.new_password = password
    user.append("roles", {"role": "Marketplace Buyer"})
    user.insert(ignore_permissions=True)
    frappe.db.commit()

    result = {"success": True, "email": email, "full_name": full_name}

    # Seller profili oluştur
    if become_seller:
        if isinstance(seller_data, str):
            try:
                seller_data = json.loads(seller_data) if seller_data else {}
            except (json.JSONDecodeError, ValueError):
                seller_data = {}
        seller_data = seller_data or {}

        seller_result = _create_seller_profile(
            user_email=email,
            seller_name=seller_data.get("seller_name") or full_name,
            phone=phone or seller_data.get("phone"),
            seller_type=seller_data.get("seller_type", "Individual"),
            company_name=seller_data.get("company_name"),
            tax_id=seller_data.get("tax_id"),
            city=seller_data.get("city"),
        )
        frappe.db.commit()
        result["seller"] = seller_result

    return result


@frappe.whitelist(allow_guest=True)
def check_email(email):
    """
    E-posta kullanılabilirlik kontrolü.

    GET /api/method/tradehub_core.api.auth.check_email?email=test@example.com
    Response: { available: bool }
    """
    exists = frappe.db.exists("User", {"email": email})
    return {"available": not bool(exists)}


@frappe.whitelist()
def update_password(current_password, new_password):
    """
    Şifre değiştir (login gerektirir).

    POST /api/method/tradehub_core.api.auth.update_password
    """
    from frappe.utils.password import check_password, update_password as _update_password

    user_email = frappe.session.user
    if user_email == "Guest":
        frappe.throw(_("Önce giriş yapmalısınız"), frappe.PermissionError)

    try:
        check_password(user_email, current_password)
    except frappe.AuthenticationError:
        frappe.throw(_("Mevcut şifre yanlış"))

    _update_password(user_email, new_password)
    frappe.db.commit()
    return {"success": True}
