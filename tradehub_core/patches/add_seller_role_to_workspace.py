import json
import frappe


def execute():
	"""Add Seller role to Satıcı Paneli workspace and update its content."""

	workspace_name = "Satıcı Paneli"

	if not frappe.db.exists("Workspace", workspace_name):
		return

	# Update workspace content (exclude Seller Profile, Seller Application, Buyer Profile)
	content = [
		{
			"id": "section-listing",
			"type": "section",
			"data": {"label": "Ürün & İlanlar", "columns": 4},
			"items": [
				{"id": "lnk-listing", "type": "shortcut", "data": {
					"label": "İlanlar", "format": "card",
					"link_to": "Listing", "type": "DocType", "icon": "es-line-package"
				}},
				{"id": "lnk-listing-variant", "type": "shortcut", "data": {
					"label": "İlan Varyantları", "format": "card",
					"link_to": "Listing Variant", "type": "DocType", "icon": "es-line-settings"
				}},
				{"id": "lnk-seller-product", "type": "shortcut", "data": {
					"label": "Satıcı Ürünleri", "format": "card",
					"link_to": "Seller Product", "type": "DocType", "icon": "es-line-tag"
				}},
			],
		},
		{
			"id": "section-category",
			"type": "section",
			"data": {"label": "Kategori & Kargo", "columns": 4},
			"items": [
				{"id": "lnk-product-category", "type": "shortcut", "data": {
					"label": "Ürün Kategorileri", "format": "card",
					"link_to": "Product Category", "type": "DocType", "icon": "es-line-layers"
				}},
				{"id": "lnk-seller-category", "type": "shortcut", "data": {
					"label": "Satıcı Kategorileri", "format": "card",
					"link_to": "Seller Category", "type": "DocType", "icon": "es-line-tag-double"
				}},
				{"id": "lnk-shipping-method", "type": "shortcut", "data": {
					"label": "Kargo Yöntemleri", "format": "card",
					"link_to": "Shipping Method", "type": "DocType", "icon": "es-line-truck"
				}},
			],
		},
		{
			"id": "section-finance",
			"type": "section",
			"data": {"label": "Finans & İletişim", "columns": 4},
			"items": [
				{"id": "lnk-seller-balance", "type": "shortcut", "data": {
					"label": "Bakiye", "format": "card",
					"link_to": "Seller Balance", "type": "DocType", "icon": "es-line-wallet"
				}},
				{"id": "lnk-seller-inquiry", "type": "shortcut", "data": {
					"label": "Sorgular", "format": "card",
					"link_to": "Seller Inquiry", "type": "DocType", "icon": "es-line-chat"
				}},
				{"id": "lnk-seller-review", "type": "shortcut", "data": {
					"label": "Değerlendirmeler", "format": "card",
					"link_to": "Seller Review", "type": "DocType", "icon": "es-line-star"
				}},
				{"id": "lnk-currency-rate", "type": "shortcut", "data": {
					"label": "Döviz Kurları", "format": "card",
					"link_to": "Currency Rate", "type": "DocType", "icon": "es-line-currency"
				}},
			],
		},
	]

	frappe.db.set_value(
		"Workspace",
		workspace_name,
		"content",
		json.dumps(content, ensure_ascii=False),
		update_modified=False,
	)

	frappe.db.commit()
