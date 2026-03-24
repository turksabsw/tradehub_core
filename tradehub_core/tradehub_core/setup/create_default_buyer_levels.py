import frappe


BUYER_LEVELS = [
	{
		"level_name": "NEW",
		"level_code": "NEW",
		"level_rank": 1,
		"is_default": 1,
		"threshold_value": 0,
		"color": "#6c757d",
		"icon": "fa-user",
		"display_name": "New Buyer",
		"description": "Entry-level for all new buyers",
	},
	{
		"level_name": "REGULAR",
		"level_code": "REGULAR",
		"level_rank": 2,
		"is_default": 0,
		"threshold_value": 500,
		"color": "#28a745",
		"icon": "fa-user-check",
		"display_name": "Regular Buyer",
		"description": "Buyers with consistent purchasing history",
	},
	{
		"level_name": "VIP",
		"level_code": "VIP",
		"level_rank": 3,
		"is_default": 0,
		"threshold_value": 5000,
		"color": "#ffc107",
		"icon": "fa-star",
		"display_name": "VIP Buyer",
		"description": "High-value buyers with significant purchase volume",
	},
	{
		"level_name": "PREMIUM",
		"level_code": "PREMIUM",
		"level_rank": 4,
		"is_default": 0,
		"threshold_value": 25000,
		"color": "#6f42c1",
		"icon": "fa-crown",
		"display_name": "Premium Buyer",
		"description": "Top-tier buyers with the highest purchase volume",
	},
]


def execute():
	"""Idempotent seed script for default Buyer Level records.

	Creates 4 Buyer Level records (NEW, REGULAR, VIP, PREMIUM)
	if they do not already exist. Safe to run multiple times.

	Note: Buyer Level autoname is field:level_name, so the document
	name equals the level_name value.
	"""
	for level_data in BUYER_LEVELS:
		level_name = level_data["level_name"]

		if frappe.db.exists("Buyer Level", level_name):
			continue

		doc = frappe.new_doc("Buyer Level")
		doc.level_name = level_data["level_name"]
		doc.level_code = level_data["level_code"]
		doc.level_rank = level_data["level_rank"]
		doc.status = "Active"
		doc.is_default = level_data["is_default"]
		doc.threshold_value = level_data["threshold_value"]
		doc.qualification_type = "Purchase Amount"
		doc.color = level_data["color"]
		doc.icon = level_data["icon"]
		doc.display_name = level_data["display_name"]
		doc.description = level_data["description"]
		doc.auto_upgrade = 1
		doc.auto_downgrade = 0
		doc.notify_on_level_change = 1

		doc.insert(ignore_permissions=True)

	frappe.db.commit()
