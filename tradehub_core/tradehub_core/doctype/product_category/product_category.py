import frappe
from frappe.utils.nestedset import NestedSet
import re

class ProductCategory(NestedSet):
    nsm_parent_field = "parent_product_category"

    def before_save(self):
        if not self.url_slug:
            self.url_slug = self.generate_slug()

    def generate_slug(self):
        slug = self.category_name.lower().strip()
        slug = re.sub(r'[^\w\s-]', '', slug)
        slug = re.sub(r'[\s_]+', '-', slug)
        slug = re.sub(r'-+', '-', slug).strip('-')
        return slug
