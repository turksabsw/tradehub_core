import frappe
from frappe.model.document import Document


class BuyerProfile(Document):
	def on_update(self):
		if self.has_value_changed("status"):
			self._sync_status()

	def _sync_status(self):
		"""Sync User.enabled and Seller Profile status when Buyer Profile status changes."""
		enabled = 1 if self.status == "Active" else 0
		frappe.db.set_value("User", self.user, "enabled", enabled)

		# Keep Seller Profile in sync (same user, same account)
		seller_profile = frappe.db.get_value("Seller Profile", {"user": self.user}, "name")
		if seller_profile:
			frappe.db.set_value("Seller Profile", seller_profile, "status", self.status)
