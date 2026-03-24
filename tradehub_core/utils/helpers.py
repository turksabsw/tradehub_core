import frappe


def get_seller_by_user(user_email: str) -> dict | None:
    """Kullanıcıya ait Seller Profile döndürür."""
    return frappe.db.get_value(
        "Seller Profile",
        {"user": user_email},
        ["name", "seller_name", "seller_code", "status", "health_score", "score_grade"],
        as_dict=True,
    )
