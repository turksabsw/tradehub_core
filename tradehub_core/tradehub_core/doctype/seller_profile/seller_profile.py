import frappe
from frappe.model.document import Document


class SellerProfile(Document):
	def on_update(self):
		if self.has_value_changed("status"):
			self._sync_status()

		self._sync_to_seller_application()

	def _sync_status(self):
		"""Sync User.enabled and Buyer Profile status when Seller Profile status changes."""
		enabled = 1 if self.status == "Active" else 0
		frappe.db.set_value("User", self.user, "enabled", enabled)

		# Keep Buyer Profile in sync (same user, same account)
		buyer_profile = frappe.db.get_value("Buyer Profile", {"user": self.user}, "name")
		if buyer_profile:
			frappe.db.set_value("Buyer Profile", buyer_profile, "status", self.status)

	def _sync_to_seller_application(self):
		"""Sync changed fields back to Seller Application."""
		app_name = frappe.db.get_value(
			"Seller Application", {"applicant_user": self.user}, "name"
		)
		if not app_name:
			return

		field_map = {
			"business_name": "business_name",
			"seller_type": "seller_type",
			"tax_id": "tax_id",
			"contact_phone": "contact_phone",
			"country": "country",
		}

		for profile_field, app_field in field_map.items():
			if self.has_value_changed(profile_field):
				frappe.db.set_value(
					"Seller Application", app_name, app_field, self.get(profile_field)
				)
