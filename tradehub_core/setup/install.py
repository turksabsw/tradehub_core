import frappe


def after_install():
	"""Create custom marketplace roles. Idempotent — safe to run on every migrate."""
	_create_marketplace_roles()
	frappe.db.commit()


def _create_marketplace_roles():
	roles = [
		{"role_name": "Buyer", "desk_access": 0},
		{"role_name": "Seller", "desk_access": 0},
		{"role_name": "Marketplace Admin", "desk_access": 1},
	]
	for role_data in roles:
		if not frappe.db.exists("Role", role_data["role_name"]):
			role = frappe.new_doc("Role")
			role.role_name = role_data["role_name"]
			role.desk_access = role_data["desk_access"]
			role.insert(ignore_permissions=True)


def cleanup_expired_tokens():
	"""Scheduled daily — Redis TTL handles expiry automatically.
	This is a placeholder for any future DB-level cleanup."""
	pass
