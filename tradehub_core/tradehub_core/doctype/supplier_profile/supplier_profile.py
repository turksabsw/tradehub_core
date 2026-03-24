import frappe
from frappe.model.document import Document
from frappe.utils import nowdate, getdate

class SupplierProfile(Document):
    def before_save(self):
        if self.established_year and not self.years_in_business:
            current_year = getdate(nowdate()).year
            self.years_in_business = current_year - self.established_year
