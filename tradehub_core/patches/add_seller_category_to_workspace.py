import frappe
import json


def execute():
	workspace_names = ["Satıcı Paneli", "Tradehub Seller"]

	for ws_name in workspace_names:
		if not frappe.db.exists("Workspace", ws_name):
			continue

		doc = frappe.get_doc("Workspace", ws_name)

		# Shortcut ekle
		existing_links = [s.link_to for s in doc.shortcuts]
		if "Seller Category" not in existing_links:
			doc.append("shortcuts", {
				"label": "Satıcı Kategorileri",
				"link_to": "Seller Category",
				"type": "DocType",
				"format": "Card",
				"color": "Grey"
			})

		# Content'e ekle
		try:
			content = json.loads(doc.content or "[]")
		except Exception:
			content = []

		sc_exists = any(
			item.get("data", {}).get("shortcut_name") == "Satıcı Kategorileri"
			for item in content
		)

		if not sc_exists:
			new_sc = {
				"id": "sc_seller_cat",
				"type": "shortcut",
				"data": {"shortcut_name": "Satıcı Kategorileri", "col": 3}
			}
			# "İletişim" header'ından önce ekle
			insert_idx = None
			for i, item in enumerate(content):
				if item.get("type") == "header" and "\u0130leti\u015fim" in item.get("data", {}).get("text", ""):
					insert_idx = i
					break
			if insert_idx is not None:
				content.insert(insert_idx, new_sc)
			else:
				content.append(new_sc)

			doc.content = json.dumps(content, ensure_ascii=False)

		doc.save(ignore_permissions=True)
		frappe.db.commit()
		print(f"{ws_name} workspace güncellendi")
