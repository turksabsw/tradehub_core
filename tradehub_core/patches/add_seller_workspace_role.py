import frappe


def execute():
	"""Add Seller role to Satıcı Paneli and Tradehub Seller workspaces."""

	for workspace_name in ("Satıcı Paneli", "Tradehub Seller"):
		if not frappe.db.exists("Workspace", workspace_name):
			continue

		existing = frappe.db.sql(
			"SELECT role FROM `tabHas Role` WHERE parenttype='Workspace' AND parent=%s",
			(workspace_name,),
			as_dict=True,
		)
		existing_roles = [r.role for r in existing]

		if "Seller" not in existing_roles:
			frappe.db.sql(
				"""
				INSERT INTO `tabHas Role`
					(name, creation, modified, modified_by, owner,
					 docstatus, idx, parent, parenttype, parentfield, role)
				VALUES
					(%s, NOW(), NOW(), 'Administrator', 'Administrator',
					 0, %s, %s, 'Workspace', 'roles', 'Seller')
				""",
				(frappe.generate_hash(length=10), len(existing_roles) + 1, workspace_name),
			)

	frappe.db.commit()
