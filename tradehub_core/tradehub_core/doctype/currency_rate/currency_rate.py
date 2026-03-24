import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class CurrencyRate(Document):
    def before_save(self):
        self.last_updated = now_datetime()
