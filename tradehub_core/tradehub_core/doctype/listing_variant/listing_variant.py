import frappe
from frappe.model.document import Document

class ListingVariant(Document):
    def autoname(self):
        if not self.variant_name:
            attrs = [d.attribute_value for d in (self.variant_attributes or [])]
            self.variant_name = " - ".join(attrs) if attrs else self.listing
