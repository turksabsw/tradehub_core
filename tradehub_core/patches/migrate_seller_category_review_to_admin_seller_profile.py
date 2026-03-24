"""
Seller Category, Seller Review ve Seller Inquiry kayıtlarındaki
seller alanını "Seller Profile" → "Admin Seller Profile" olarak günceller.

Eşleştirme: Seller Profile.user → Admin Seller Profile.user
"""
import frappe


def execute():
	if not frappe.db.table_exists("tabSeller Profile"):
		return

	# user → admin_seller_profile.name eşleme tablosu oluştur
	admin_profiles = frappe.get_all(
		"Admin Seller Profile",
		fields=["name", "user"],
	)
	user_to_admin = {p.user: p.name for p in admin_profiles if p.user}

	if not user_to_admin:
		return

	# Seller Profile → user eşlemesi
	seller_profiles = frappe.get_all(
		"Seller Profile",
		fields=["name", "user"],
	)
	old_to_new = {}
	for sp in seller_profiles:
		if sp.user and sp.user in user_to_admin:
			old_to_new[sp.name] = user_to_admin[sp.user]

	if not old_to_new:
		return

	# Seller Category güncelle
	for old_name, new_name in old_to_new.items():
		frappe.db.sql(
			"UPDATE `tabSeller Category` SET seller=%s WHERE seller=%s",
			(new_name, old_name),
		)

	# Seller Review güncelle
	for old_name, new_name in old_to_new.items():
		frappe.db.sql(
			"UPDATE `tabSeller Review` SET seller=%s WHERE seller=%s",
			(new_name, old_name),
		)

	# Seller Inquiry güncelle
	for old_name, new_name in old_to_new.items():
		frappe.db.sql(
			"UPDATE `tabSeller Inquiry` SET seller=%s, seller_code=%s WHERE seller=%s",
			(new_name, new_name, old_name),
		)

	frappe.db.commit()
