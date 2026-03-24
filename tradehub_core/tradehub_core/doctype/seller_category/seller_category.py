import frappe
from frappe import _
from frappe.model.document import Document


def _is_admin():
    return frappe.session.user == "Administrator" or "System Manager" in frappe.get_roles()


class SellerCategory(Document):
    def before_insert(self):
        if not _is_admin():
            self.status = "Pending"

    def validate(self):
        if _is_admin():
            return
        # Satıcı sadece kendi profiline kategori ekleyebilir
        seller_profile = _get_seller_profile()
        if not seller_profile:
            frappe.throw(_("Satıcı profili bulunamadı."))
        if self.seller and self.seller != seller_profile:
            frappe.throw(_("Başka bir satıcı adına kategori ekleyemezsiniz."))
        self.seller = seller_profile
        # Satıcı status'ü değiştiremez
        if not self.is_new():
            old_status = frappe.db.get_value("Seller Category", self.name, "status")
            if old_status != self.status:
                self.status = old_status


def _get_seller_profile():
    user = frappe.session.user
    profile = frappe.db.get_value("Admin Seller Profile", {"owner": user}, "name")
    if not profile:
        profile = frappe.db.get_value("Admin Seller Profile", {"email": user}, "name")
    return profile
