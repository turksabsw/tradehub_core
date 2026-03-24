import json
import frappe
from frappe import _
from frappe.utils import now_datetime

def _invalidate_cart_cache(_cart_name):
	"""No-op: cache kaldırıldı. Listing status değiştiğinde stale data önlemek için."""
	pass


def _build_cart_response_cached(cart_name):
	"""Cache kaldırıldı — her istek DB'den taze veri çeker."""
	return _build_cart_response(cart_name)


# ──────────────────────────── helpers ────────────────────────────────────────

def _get_or_create_cart(user):
	"""Get or create the active Cart for a user. Returns cart name."""
	cart_name = frappe.db.get_value("Cart", {"buyer": user, "status": "Active"}, "name")
	if not cart_name:
		try:
			cart = frappe.new_doc("Cart")
			cart.buyer = user
			cart.status = "Active"
			cart.insert(ignore_permissions=True)
			frappe.db.commit()
			cart_name = cart.name
		except frappe.DuplicateEntryError:
			# Race condition: another request created the cart first
			frappe.db.rollback()
			cart_name = frappe.db.get_value("Cart", {"buyer": user, "status": "Active"}, "name")
	return cart_name


def _verify_cart_item_owner(cart_item_name, user):
	"""Raises PermissionError if cart_item doesn't belong to user. Returns cart name."""
	# Use filter syntax for child table lookup (safer than positional string arg)
	rows = frappe.get_all(
		"Cart Item",
		filters={"name": cart_item_name},
		fields=["parent"],
	)
	if not rows:
		frappe.throw(_("Sepet öğesi bulunamadı"), frappe.DoesNotExistError)

	parent_cart = rows[0]["parent"]
	cart_buyer = frappe.db.get_value("Cart", parent_cart, "buyer")
	if cart_buyer != user:
		frappe.throw(_("Yetkisiz işlem"), frappe.PermissionError)
	return parent_cart


def _find_existing_cart_item(cart_name, listing, listing_variant):
	"""
	Find an existing Cart Item for the given listing+variant combination.
	Handles NULL vs empty string safely by comparing in Python.
	"""
	all_rows = frappe.get_all(
		"Cart Item",
		filters={"parent": cart_name, "listing": listing},
		fields=["name", "listing_variant", "quantity"],
	)
	norm_variant = listing_variant or None
	for row in all_rows:
		row_variant = row.listing_variant or None
		if row_variant == norm_variant:
			return row
	return None


def _get_inline_variant_stock(listing_name, synthetic_variant_id):
	"""
	Synthetic variantId formatı: "{listing_name}-{attribute_type}-{attribute_value}"
	Listing Variant Item child table'dan variant_stock değerini döndürür.
	Eşleşme bulunamazsa None döner.
	"""
	prefix = listing_name + "-"
	if not synthetic_variant_id or not synthetic_variant_id.startswith(prefix):
		return None
	remainder = synthetic_variant_id[len(prefix):]
	inline_variants = frappe.get_all(
		"Listing Variant Item",
		filters={"parent": listing_name, "parenttype": "Listing"},
		fields=["attribute_type", "attribute_value", "variant_stock"],
	)
	for iv in inline_variants:
		expected = f"{iv.attribute_type}-{iv.attribute_value}"
		if remainder == expected:
			return float(iv.variant_stock or 0)
	return None


def _check_stock(listing_doc, listing_name, listing_variant, total_qty):
	"""
	Stok kontrolü: track_inventory açıksa toplam miktarı (mevcut + yeni) kontrol et.
	Sırasıyla: 1) Listing Variant doc stoğu, 2) inline variant item stoğu, 3) listing stoğu.
	"""
	if not listing_doc.track_inventory or listing_doc.allow_backorders:
		return  # Stok takibi kapalı veya backorder açık — kontrol gerekmez

	available = None

	if listing_variant:
		# 1) Gerçek Listing Variant doc'u dene
		variant_doc = frappe.db.get_value(
			"Listing Variant", listing_variant, ["stock_qty"], as_dict=True
		)
		if variant_doc and (variant_doc.stock_qty or 0) > 0:
			available = float(variant_doc.stock_qty)
		else:
			# 2) Inline variant item dene (synthetic ID: "{listing}-{type}-{value}")
			inline_stock = _get_inline_variant_stock(listing_name, listing_variant)
			if inline_stock is not None:
				available = inline_stock

	if available is None:
		# 3) Listing seviyesi stok
		available = float(listing_doc.stock_qty or 0)

	if total_qty > available:
		if available <= 0:
			frappe.throw(_("Bu üründen yeterli stok bulunmamaktadır."))
		else:
			frappe.throw(_("Bu üründen bu kadar stok yok. En fazla {0} adet eklenebilir.").format(int(available)))


def _build_cart_response(cart_name):
	"""
	Build a CartSupplier[] response from a Cart document.
	Matches the frontend's CartSupplier / CartProduct / CartSku interfaces exactly.
	"""
	items = frappe.get_all(
		"Cart Item",
		filters={"parent": cart_name},
		fields=["name", "listing", "listing_variant", "quantity",
				"seller", "snapshot_title", "snapshot_image", "snapshot_price", "snapshot_currency"],
		order_by="creation asc",
	)

	# seller_id → {supplier dict with products dict}
	sellers_map = {}

	for item in items:
		listing = frappe.db.get_value(
			"Listing",
			item.listing,
			["name", "title", "seller_profile", "primary_image",
			 "selling_price", "base_price", "min_order_qty", "currency", "status",
			 "track_inventory", "allow_backorders", "stock_qty"],
			as_dict=True,
		)
		is_available = bool(listing and listing.status == "Active")

		# Satıcı ID'sini belirle: aktif listing'den veya snapshot'tan al
		if is_available:
			seller_id = listing.seller_profile
		else:
			seller_id = item.seller or (listing.seller_profile if listing else None)

		if not seller_id:
			continue

		# Listing yok ve snapshot da yok → gösterecek veri yok, atla
		if not listing and not item.snapshot_title and not item.snapshot_price:
			continue

		# Lazy-init seller bucket
		if seller_id not in sellers_map:
			seller = frappe.db.get_value(
				"Admin Seller Profile",
				seller_id,
				["seller_name", "seller_code", "logo"],
				as_dict=True,
			)
			if not seller:
				continue
			slug = seller.seller_code or seller_id
			sellers_map[seller_id] = {
				"id": seller_id,
				"name": seller.seller_name or seller_id,
				"href": f"/pages/seller.html?id={slug}",
				"selected": True,
				"products": {},
			}

		listing_name = item.listing

		# Lazy-init product bucket
		if listing_name not in sellers_map[seller_id]["products"]:
			if is_available:
				tiers = frappe.get_all(
					"Listing Bulk Pricing Tier",
					filters={"parent": listing_name},
					fields=["min_qty", "max_qty", "price"],
					order_by="min_qty asc",
				)
				price_tiers = [
					{"minQty": t.min_qty, "maxQty": t.max_qty or None, "price": float(t.price)}
					for t in tiers
				]
				sellers_map[seller_id]["products"][listing_name] = {
					"id": listing_name,
					"title": listing.title or "",
					"href": f"/pages/product.html?id={listing_name}",
					"tags": [],
					"moqLabel": f"Min. {listing.min_order_qty or 1} Adet",
					"favoriteIcon": "♡",
					"deleteIcon": "🗑",
					"selected": True,
					"priceTiers": price_tiers,
					"baseCurrency": listing.currency or "USD",
					"skus": [],
				}
			else:
				# Snapshot yoksa listing verisini fallback olarak kullan
				snap_title = item.snapshot_title or (listing.title if listing else "") or ""
				snap_image = item.snapshot_image or (listing.primary_image if listing else "") or ""
				snap_price_raw = float(item.snapshot_price or 0) or float(
					(listing.selling_price or listing.base_price or 0) if listing else 0
				)
				snap_currency = item.snapshot_currency or (listing.currency if listing else "USD") or "USD"
				sellers_map[seller_id]["products"][listing_name] = {
					"id": listing_name,
					"title": snap_title,
					"href": f"/pages/product.html?id={listing_name}",
					"tags": [],
					"moqLabel": "",
					"favoriteIcon": "♡",
					"deleteIcon": "🗑",
					"selected": False,
					"priceTiers": [],
					"baseCurrency": snap_currency,
					"skus": [],
				}

		if is_available:
			# Canlı veri ile SKU oluştur
			base_price = float(listing.selling_price or listing.base_price or 0)
			sku_image = listing.primary_image or ""
			variant_text = ""
			base_price_addon = 0.0

			variant = None
			if item.listing_variant:
				variant = frappe.db.get_value(
					"Listing Variant",
					item.listing_variant,
					["variant_name", "price", "primary_image", "stock_qty"],
					as_dict=True,
				)
				if variant:
					variant_text = variant.variant_name or ""
					if variant.primary_image:
						sku_image = variant.primary_image
					if variant.price:
						base_price_addon = float(variant.price) - base_price

			# maxQty: track_inventory açıksa variant stoğunu, yoksa listing stoğunu kullan
			if listing.track_inventory and not listing.allow_backorders:
				if variant:
					max_qty = max(0, int(variant.stock_qty or 0))
				else:
					max_qty = max(0, int(listing.stock_qty or 0))
			else:
				max_qty = 999999

			sellers_map[seller_id]["products"][listing_name]["skus"].append({
				"id": item.name,
				"skuImage": sku_image,
				"variantText": variant_text,
				"unitPrice": base_price + base_price_addon,
				"priceAddon": base_price_addon,
				"currency": listing.currency or "USD",
				"unit": "Adet",
				"quantity": item.quantity,
				"minQty": listing.min_order_qty or 1,
				"maxQty": max_qty,
				"selected": True,
				"baseUnitPrice": base_price,
				"basePriceAddon": base_price_addon,
				"baseCurrency": listing.currency or "USD",
				"listingVariant": item.listing_variant or None,
				"isAvailable": True,
			})
		else:
			# Snapshot verisiyle SKU oluştur (satın alınamaz, gösterim amaçlı)
			# snap_title/image/price/currency product bucket'ta hesaplandı, aynısını kullan
			snap_price_sku = float(item.snapshot_price or 0) or float(
				(listing.selling_price or listing.base_price or 0) if listing else 0
			)
			snap_image_sku = item.snapshot_image or (listing.primary_image if listing else "") or ""
			snap_currency_sku = item.snapshot_currency or (listing.currency if listing else "USD") or "USD"
			sellers_map[seller_id]["products"][listing_name]["skus"].append({
				"id": item.name,
				"skuImage": snap_image_sku,
				"variantText": "",
				"unitPrice": snap_price_sku,
				"priceAddon": 0,
				"currency": snap_currency_sku,
				"unit": "Adet",
				"quantity": item.quantity,
				"minQty": 1,
				"maxQty": 999999,
				"selected": False,
				"baseUnitPrice": snap_price_sku,
				"basePriceAddon": 0,
				"baseCurrency": snap_currency_sku,
				"listingVariant": item.listing_variant or None,
				"isAvailable": False,
			})

	suppliers = []
	for seller_data in sellers_map.values():
		suppliers.append({
			"id": seller_data["id"],
			"name": seller_data["name"],
			"href": seller_data["href"],
			"selected": seller_data["selected"],
			"products": list(seller_data["products"].values()),
		})

	return {"suppliers": suppliers}


# ──────────────────────────── endpoints ──────────────────────────────────────

@frappe.whitelist()
def get_cart():
	"""Return the current user's cart as CartSupplier[]."""
	user = frappe.session.user
	if not user or user == "Guest":
		return {"suppliers": []}

	cart_name = frappe.db.get_value("Cart", {"buyer": user, "status": "Active"}, "name")
	if not cart_name:
		return {"suppliers": []}

	return _build_cart_response_cached(cart_name)


@frappe.whitelist()
def check_stock(listing, quantity=1, listing_variant=None):
	"""
	Stok kontrolü yapar ama sepete eklemez.
	Ürün sayfasındaki drawer için kullanılır — gerçek kayıt cart.add_to_cart ile yapılır.
	Hata yoksa {"ok": True} döner, hata varsa frappe.throw() ile exception fırlatır.
	"""
	if not frappe.db.exists("Listing", listing):
		frappe.throw(_("Ürün bulunamadı"), frappe.DoesNotExistError)

	listing_doc = frappe.db.get_value(
		"Listing",
		listing,
		["status", "stock_qty", "track_inventory", "allow_backorders"],
		as_dict=True,
	)
	if listing_doc.status != "Active":
		frappe.throw(_("Bu ürün şu an satışta değil"))

	qty = int(quantity)
	listing_variant = listing_variant or None
	_check_stock(listing_doc, listing, listing_variant, qty)
	return {"ok": True}


@frappe.whitelist()
def add_to_cart(listing, quantity=1, listing_variant=None):
	"""
	Add a listing (optionally a specific variant) to cart.
	If already exists, increments quantity.
	Returns the full cart response.
	"""
	user = frappe.session.user
	if not user or user == "Guest":
		frappe.throw(_("Sepete eklemek için giriş yapmanız gerekiyor"), frappe.AuthenticationError)

	if not frappe.db.exists("Listing", listing):
		frappe.throw(_("Ürün bulunamadı"), frappe.DoesNotExistError)

	# Listing durumu ve stok/MOQ kontrolü
	listing_doc = frappe.db.get_value(
		"Listing",
		listing,
		["status", "min_order_qty", "stock_qty", "track_inventory", "allow_backorders",
		 "seller_profile", "title", "primary_image", "selling_price", "base_price", "currency"],
		as_dict=True,
	)
	if listing_doc.status != "Active":
		frappe.throw(_("Bu ürün şu an satışta değil"))

	qty = int(quantity)
	min_qty = int(listing_doc.min_order_qty or 1)
	if qty < min_qty:
		frappe.throw(_("Minimum sipariş miktarı: {0}").format(min_qty))

	# Normalize variant: empty string → None
	listing_variant = listing_variant or None

	cart_name = _get_or_create_cart(user)
	existing_row = _find_existing_cart_item(cart_name, listing, listing_variant)
	existing_qty = existing_row.quantity if existing_row else 0
	total_qty = existing_qty + qty

	_check_stock(listing_doc, listing, listing_variant, total_qty)

	# Snapshot verisi hazırla
	snap_price = float(listing_doc.selling_price or listing_doc.base_price or 0)
	snap_image = listing_doc.primary_image or ""
	snap_title = listing_doc.title or ""
	snap_currency = listing_doc.currency or "USD"
	seller_id = listing_doc.seller_profile or None

	if listing_variant:
		var_snap = frappe.db.get_value(
			"Listing Variant", listing_variant,
			["primary_image", "price"], as_dict=True
		)
		if var_snap:
			if var_snap.primary_image:
				snap_image = var_snap.primary_image
			if var_snap.price:
				snap_price = float(var_snap.price)

	# cart_name ve existing_row yukarıda stok kontrolü için alındı — tekrar sorgulama
	if existing_row:
		frappe.db.set_value("Cart Item", existing_row.name, "quantity", existing_row.quantity + qty)
	else:
		cart_doc = frappe.get_doc("Cart", cart_name)
		cart_doc.append("items", {
			"listing": listing,
			"listing_variant": listing_variant,
			"quantity": qty,
			"seller": seller_id,
			"snapshot_title": snap_title,
			"snapshot_image": snap_image,
			"snapshot_price": snap_price,
			"snapshot_currency": snap_currency,
		})
		cart_doc.save(ignore_permissions=True)

	frappe.db.commit()
	_invalidate_cart_cache(cart_name)
	return _build_cart_response_cached(cart_name)


@frappe.whitelist()
def update_cart_item(cart_item, quantity):
	"""Update the quantity of a cart item."""
	user = frappe.session.user
	if not user or user == "Guest":
		frappe.throw(_("Giriş yapmanız gerekiyor"), frappe.AuthenticationError)

	_verify_cart_item_owner(cart_item, user)

	qty = int(quantity)
	if qty <= 0:
		frappe.throw(_("Miktar sıfırdan büyük olmalıdır"))

	# Stok kontrolü — variant varsa variant stoğu kullanılır (MOQ kontrolü checkout'ta)
	listing_name, listing_variant_name = frappe.db.get_value(
		"Cart Item", cart_item, ["listing", "listing_variant"]
	) or (None, None)
	if listing_name:
		listing_doc = frappe.db.get_value(
			"Listing",
			listing_name,
			["stock_qty", "track_inventory", "allow_backorders"],
			as_dict=True,
		)
		if listing_doc:
			_check_stock(listing_doc, listing_name, listing_variant_name, qty)

	frappe.db.set_value("Cart Item", cart_item, "quantity", qty)
	frappe.db.commit()
	cart_name = frappe.db.get_value("Cart", {"buyer": user, "status": "Active"}, "name")
	if cart_name:
		_invalidate_cart_cache(cart_name)
	return {"success": True}


@frappe.whitelist()
def remove_cart_item(cart_item):
	"""Remove a single item from cart."""
	user = frappe.session.user
	if not user or user == "Guest":
		frappe.throw(_("Giriş yapmanız gerekiyor"), frappe.AuthenticationError)

	cart_name = _verify_cart_item_owner(cart_item, user)
	frappe.delete_doc("Cart Item", cart_item, ignore_permissions=True)
	frappe.db.commit()
	_invalidate_cart_cache(cart_name)
	return {"success": True}


@frappe.whitelist()
def clear_cart():
	"""Remove all items from the active cart."""
	user = frappe.session.user
	if not user or user == "Guest":
		frappe.throw(_("Giriş yapmanız gerekiyor"), frappe.AuthenticationError)

	cart_name = frappe.db.get_value("Cart", {"buyer": user, "status": "Active"}, "name")
	if not cart_name:
		return {"success": True}

	frappe.db.delete("Cart Item", {"parent": cart_name})
	frappe.db.commit()
	_invalidate_cart_cache(cart_name)
	return {"success": True}


@frappe.whitelist()
def merge_guest_cart(items):
	"""
	Merge guest localStorage items into the logged-in user's cart.
	items: JSON list of {listing, listing_variant?, quantity}
	Returns the full cart response after merge.
	"""
	user = frappe.session.user
	if not user or user == "Guest":
		frappe.throw(_("Giriş yapmanız gerekiyor"), frappe.AuthenticationError)

	if isinstance(items, str):
		try:
			items = json.loads(items)
		except (ValueError, TypeError):
			frappe.throw(_("Geçersiz sepet verisi"))

	if not isinstance(items, list):
		frappe.throw(_("Geçersiz sepet verisi"))

	cart_name = _get_or_create_cart(user)

	# Load cart doc once for appending new items
	cart_doc = frappe.get_doc("Cart", cart_name)
	needs_save = False

	for item in items:
		listing = item.get("listing")
		listing_variant = item.get("listing_variant") or None
		qty = int(item.get("quantity", 1))

		if not listing or qty <= 0:
			continue
		if not frappe.db.exists("Listing", listing):
			continue

		existing_row = _find_existing_cart_item(cart_name, listing, listing_variant)

		if existing_row:
			# Update in-memory cart_doc row so save() is consistent
			for row in cart_doc.items:
				if row.name == existing_row.name:
					row.quantity = existing_row.quantity + qty
					needs_save = True
					break
		else:
			# Snapshot verisi hazırla
			listing_snap = frappe.db.get_value(
				"Listing", listing,
				["seller_profile", "title", "primary_image", "selling_price", "base_price", "currency"],
				as_dict=True,
			) or {}
			snap_price = float(listing_snap.get("selling_price") or listing_snap.get("base_price") or 0)
			snap_image = listing_snap.get("primary_image") or ""
			snap_currency = listing_snap.get("currency") or "USD"
			if listing_variant:
				var_snap = frappe.db.get_value(
					"Listing Variant", listing_variant,
					["primary_image", "price"], as_dict=True
				)
				if var_snap:
					if var_snap.primary_image:
						snap_image = var_snap.primary_image
					if var_snap.price:
						snap_price = float(var_snap.price)
			cart_doc.append("items", {
				"listing": listing,
				"listing_variant": listing_variant,
				"quantity": qty,
				"seller": listing_snap.get("seller_profile") or None,
				"snapshot_title": listing_snap.get("title") or "",
				"snapshot_image": snap_image,
				"snapshot_price": snap_price,
				"snapshot_currency": snap_currency,
			})
			needs_save = True

	if needs_save:
		cart_doc.save(ignore_permissions=True)

	frappe.db.commit()
	_invalidate_cart_cache(cart_name)
	return _build_cart_response_cached(cart_name)


@frappe.whitelist()
def create_order(orders_json, shipping_address=None, payment_method=None, coupon_code=None, coupon_discount=0):
	"""
	Seçili sepet ürünlerinden sipariş(ler) oluşturur.
	orders_json: JSON list of {
	  seller_id, seller_name, shipping_fee, currency,
	  products: [{listing, listing_title, variation, unit_price, quantity, total_price, image}]
	}
	Returns: { orders: [{order_name, order_number, seller_name, total}] }
	"""
	user = frappe.session.user
	if not user or user == "Guest":
		frappe.throw(_("Sipariş vermek için giriş yapmanız gerekiyor"), frappe.AuthenticationError)

	if isinstance(orders_json, str):
		try:
			orders_data = json.loads(orders_json)
		except (ValueError, TypeError):
			frappe.throw(_("Geçersiz sipariş verisi"))
	else:
		orders_data = orders_json

	if not isinstance(orders_data, list) or len(orders_data) == 0:
		frappe.throw(_("Sipariş verisi boş olamaz"))

	order_count = len([o for o in orders_data if o.get("products")])
	coupon_discount_val = float(coupon_discount or 0)
	# Kupon indirimini siparişlere eşit dağıt
	per_order_coupon_discount = round(coupon_discount_val / order_count, 2) if order_count > 0 else 0

	created_orders = []

	for order_data in orders_data:
		seller_id = order_data.get("seller_id", "")
		products = order_data.get("products", [])
		shipping_fee = float(order_data.get("shipping_fee", 0))
		currency = order_data.get("currency", "USD")

		if not products:
			continue

		subtotal = sum(float(p.get("total_price", 0)) for p in products)
		total = subtotal + shipping_fee - per_order_coupon_discount

		order_doc = frappe.new_doc("Order")
		order_doc.buyer = user
		order_doc.seller = seller_id if frappe.db.exists("Admin Seller Profile", seller_id) else None
		order_doc.status = "Ödeme Bekleniyor"
		order_doc.payment_method = payment_method or "bank_transfer"
		order_doc.currency = currency
		order_doc.subtotal = subtotal
		order_doc.shipping_fee = shipping_fee
		order_doc.coupon_code = coupon_code or ""
		order_doc.coupon_discount = per_order_coupon_discount
		order_doc.total = max(0, total)
		order_doc.shipping_address = shipping_address or ""
		order_doc.shipping_method = order_data.get("shipping_method", "")
		order_doc.ship_from = order_data.get("ship_from", "")
		order_doc.buyer_note = order_data.get("buyer_note", "") or ""

		for p in products:
			lv = p.get("listing_variant") or None
			if lv and not frappe.db.exists("Listing Variant", lv):
				lv = None
			order_doc.append("items", {
				"listing": p.get("listing") if p.get("listing") and frappe.db.exists("Listing", p.get("listing")) else None,
				"listing_title": p.get("listing_title", ""),
				"listing_variant": lv,
				"variation": p.get("variation", ""),
				"unit_price": float(p.get("unit_price", 0)),
				"quantity": int(p.get("quantity", 1)),
				"total_price": float(p.get("total_price", 0)),
				"image": p.get("image", ""),
			})

		order_doc.insert(ignore_permissions=True)
		created_orders.append({
			"order_name": order_doc.name,
			"order_number": order_doc.name,
			"seller_name": order_data.get("seller_name", ""),
			"total": max(0, total),
			"currency": currency,
		})

	if not created_orders:
		frappe.throw(_("Hiçbir sipariş oluşturulamadı"))

	# Kupon used_count artır
	if coupon_code:
		coupon_name = frappe.db.get_value("Coupon", {"code": coupon_code.strip().upper(), "is_active": 1}, "name")
		if coupon_name:
			current_count = int(frappe.db.get_value("Coupon", coupon_name, "used_count") or 0)
			frappe.db.set_value("Coupon", coupon_name, "used_count", current_count + 1)

	# Sepeti temizle
	cart_name = frappe.db.get_value("Cart", {"buyer": user, "status": "Active"}, "name")
	if cart_name:
		frappe.db.delete("Cart Item", {"parent": cart_name})

	frappe.db.commit()
	return {"orders": created_orders}


@frappe.whitelist()
def get_orders(page=1, page_size=20):
	"""Oturumdaki kullanıcının siparişlerini döndürür. order.py'ye proxy."""
	from tradehub_core.api.order import get_my_orders
	return get_my_orders(page=page, page_size=page_size)


@frappe.whitelist(allow_guest=True)
def validate_coupon(code, order_total=0):
	"""
	Kupon kodunu doğrular ve indirim bilgisini döndürür.
	"""
	if not code:
		frappe.throw(_("Kupon kodu boş olamaz"))

	import datetime
	coupon = frappe.db.get_value(
		"Coupon",
		{"code": code.strip().upper(), "is_active": 1},
		["name", "code", "coupon_type", "value", "min_order", "max_uses", "used_count", "description", "expires_at"],
		as_dict=True,
	)

	if not coupon:
		frappe.throw(_("Geçersiz veya süresi dolmuş kupon kodu"))

	# Son geçerlilik tarihi kontrolü
	if coupon.expires_at:
		today = datetime.date.today()
		if coupon.expires_at < today:
			frappe.throw(_("Bu kuponun süresi dolmuş"))

	# Max kullanım kontrolü
	if coupon.max_uses and int(coupon.max_uses) > 0:
		if int(coupon.used_count or 0) >= int(coupon.max_uses):
			frappe.throw(_("Bu kupon maksimum kullanım sayısına ulaştı"))

	# Min sipariş tutarı kontrolü
	order_amount = float(order_total or 0)
	min_order = float(coupon.min_order or 0)
	if min_order > 0 and order_amount < min_order:
		frappe.throw(_("Bu kupon için minimum sipariş tutarı: {0}").format(min_order))

	return {
		"code": coupon.code,
		"type": coupon.coupon_type,
		"value": float(coupon.value or 0),
		"minOrder": float(coupon.min_order or 0),
		"description": coupon.description or "",
	}


@frappe.whitelist()
def get_buyer_coupons():
	"""Sisteme tanımlı aktif kuponları ve durumlarını döndürür."""
	import datetime
	today = datetime.date.today()

	coupons = frappe.get_all(
		"Coupon",
		filters={"is_active": 1},
		fields=["name", "code", "coupon_type", "value", "min_order", "max_uses", "used_count", "description", "expires_at"],
		order_by="creation desc",
	)

	result = []
	for c in coupons:
		if c.expires_at and c.expires_at < today:
			status = "expired"
		elif int(c.max_uses or 0) > 0 and int(c.used_count or 0) >= int(c.max_uses or 0):
			status = "used"
		else:
			status = "available"

		result.append({
			"code": c.code,
			"type": c.coupon_type,
			"value": float(c.value or 0),
			"minOrder": float(c.min_order or 0),
			"description": c.description or "",
			"status": status,
			"expiresAt": str(c.expires_at) + "T23:59:59Z" if c.expires_at else "",
		})

	return {"coupons": result}
