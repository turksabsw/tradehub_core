import hashlib

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit


def _generate_member_id(email: str, creation) -> str:
	"""Generate a deterministic, unique member ID from email + creation timestamp."""
	raw = f"{email}:{creation}"
	digest = hashlib.sha256(raw.encode()).hexdigest()[:8].upper()
	return f"TH-{digest}"


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=30, seconds=300)
def check_email_exists(email: str):
	"""Check whether an email is already registered as a User.

	Returns exists=True and disabled=True if the user account was deactivated.
	"""
	email = (email or "").strip().lower()
	user_data = frappe.db.get_value("User", email, ["name", "enabled"], as_dict=True)
	if not user_data:
		return {"success": True, "exists": False, "disabled": False}

	disabled = not user_data.enabled
	return {"success": True, "exists": True, "disabled": disabled}


@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_session_user():
	"""Return current session user info with role flags.

	Returns logged_in: false for guests instead of throwing 403.
	"""
	if frappe.session.user == "Guest":
		return {"logged_in": False, "user": None}

	user_data = frappe.db.get_value(
		"User",
		frappe.session.user,
		["email", "full_name", "first_name", "last_name", "creation"],
		as_dict=True,
	)

	if not user_data:
		return {"logged_in": False, "user": None}

	roles = frappe.get_roles(frappe.session.user)

	is_admin = "System Manager" in roles or "Administrator" in roles
	is_buyer = "Buyer" in roles
	is_seller = "Seller" in roles

	has_seller_profile = bool(
		frappe.db.exists(
			"Seller Profile",
			{"user": frappe.session.user, "status": "Active"},
		)
	)

	# Seller Application status (exact value for frontend routing)
	seller_application_status = (
		frappe.db.get_value(
			"Seller Application",
			{"applicant_user": frappe.session.user},
			"status",
		)
		or None
	)

	pending_seller_application = seller_application_status in (
		"Draft", "Submitted", "Under Review",
	)

	rejected_seller_application = seller_application_status == "Rejected"

	seller_profile = (
		frappe.db.get_value(
			"Seller Profile", {"user": frappe.session.user}, "name"
		)
		or None
	)

	# Admin Seller Profile — satıcının mağaza profili (filtreleme için seller_code gerekli)
	admin_seller_profile = None
	if is_seller:
		try:
			asp = frappe.db.get_value(
				"Admin Seller Profile",
				{"seller_profile": frappe.session.user},
				["name", "seller_code"],
				as_dict=True,
			)
			if asp:
				admin_seller_profile = {
					"name": asp.name,
					"seller_code": asp.seller_code or asp.name,
				}
		except Exception:
			pass

	member_id = _generate_member_id(user_data.email, user_data.creation)

	return {
		"logged_in": True,
		"user": {
			"email": user_data.email,
			"full_name": user_data.full_name,
			"first_name": user_data.first_name or "",
			"last_name": user_data.last_name or "",
			"member_id": member_id,
			"roles": roles,
			"is_admin": is_admin,
			"is_seller": is_seller,
			"is_buyer": is_buyer,
			"has_seller_profile": has_seller_profile,
			"pending_seller_application": pending_seller_application,
			"rejected_seller_application": rejected_seller_application,
			"seller_application_status": seller_application_status,
			"seller_profile": seller_profile,
			"admin_seller_profile": admin_seller_profile,
		},
	}


@frappe.whitelist(methods=["GET"])
def get_user_profile():
	"""Return detailed profile data for the currently logged-in user.

	Detects account type (buyer/seller) and returns role-specific fields.
	"""
	user = frappe.session.user
	if user == "Guest":
		frappe.throw(_("Not logged in."), frappe.AuthenticationError)

	user_data = frappe.db.get_value(
		"User",
		user,
		["email", "full_name", "first_name", "last_name", "creation", "phone"],
		as_dict=True,
	)

	if not user_data:
		frappe.throw(_("User not found."), frappe.DoesNotExistError)

	roles = frappe.get_roles(user)

	# Detect seller: has Seller role, Seller Profile, or Seller Application
	is_seller = (
		"Seller" in roles
		or frappe.db.exists("Seller Profile", {"user": user})
		or frappe.db.exists("Seller Application", {"applicant_user": user})
	)

	# Read member_id from DB; fallback to computed value for legacy users
	member_id = (
		frappe.db.get_value("Buyer Profile", {"user": user}, "member_id")
		or frappe.db.get_value("Seller Profile", {"user": user}, "member_id")
		or frappe.db.get_value("Seller Application", {"applicant_user": user}, "member_id")
		or _generate_member_id(user_data.email, user_data.creation)
	)

	base = {
		"member_id": member_id,
		"first_name": user_data.first_name or "",
		"last_name": user_data.last_name or "",
		"full_name": user_data.full_name or "",
		"email": user_data.email,
		"phone": user_data.phone or "",
	}

	# ── Seller ──
	if is_seller:
		base["account_type"] = "seller"

		# Try Seller Profile first (approved sellers)
		sp = frappe.db.get_value(
			"Seller Profile", {"user": user},
			["seller_name", "seller_type", "business_name", "tax_id",
			 "contact_phone", "country", "status"],
			as_dict=True,
		)
		if sp:
			base.update({
				"seller_type": sp.seller_type or "",
				"business_name": sp.business_name or "",
				"tax_id": sp.tax_id or "",
				"phone": sp.contact_phone or user_data.phone or "",
				"country": sp.country or "",
				"seller_status": sp.status or "",
			})
			return base

		# Fallback: Seller Application (pending sellers)
		sa = frappe.db.get_value(
			"Seller Application", {"applicant_user": user},
			["seller_type", "business_name", "contact_phone", "tax_id",
			 "tax_id_type", "tax_office", "address_line_1", "city",
			 "country", "bank_name", "iban", "account_holder_name", "status"],
			as_dict=True,
		)
		if sa:
			base.update({
				"seller_type": sa.seller_type or "",
				"business_name": sa.business_name or "",
				"tax_id": sa.tax_id or "",
				"tax_id_type": sa.tax_id_type or "",
				"tax_office": sa.tax_office or "",
				"address": sa.address_line_1 or "",
				"city": sa.city or "",
				"phone": sa.contact_phone or user_data.phone or "",
				"country": sa.country or "",
				"bank_name": sa.bank_name or "",
				"iban": sa.iban or "",
				"account_holder_name": sa.account_holder_name or "",
				"application_status": sa.status or "",
			})
		return base

	# ── Buyer (default) ──
	base["account_type"] = "buyer"
	buyer_data = frappe.db.get_value(
		"Buyer Profile", {"user": user},
		["country", "phone", "email_verified"],
		as_dict=True,
	) or {}
	base.update({
		"email_verified": bool(buyer_data.get("email_verified")),
		"phone": user_data.phone or buyer_data.get("phone", "") or "",
		"country": buyer_data.get("country", "") or "",
	})
	return base


@frappe.whitelist(methods=["POST"])
def update_user_profile(
	first_name: str = None,
	last_name: str = None,
	phone: str = None,
	country: str = None,
	business_name: str = None,
	address: str = None,
	city: str = None,
):
	"""Update profile fields for the currently logged-in user.

	Updates User doc + Buyer Profile (buyers) or Seller Application (sellers).
	"""
	user = frappe.session.user
	if user == "Guest":
		frappe.throw(_("Not logged in."), frappe.AuthenticationError)

	doc = frappe.get_doc("User", user)

	if first_name is not None:
		doc.first_name = first_name
	if last_name is not None:
		doc.last_name = last_name
	if phone is not None:
		doc.phone = phone

	doc.save(ignore_permissions=True)

	# ── Seller: update Seller Application or Seller Profile ──
	seller_app = frappe.db.get_value("Seller Application", {"applicant_user": user}, "name")
	seller_profile = frappe.db.get_value("Seller Profile", {"user": user}, "name")

	if seller_profile:
		updates = {}
		if first_name is not None or last_name is not None:
			fn = first_name if first_name is not None else doc.first_name
			ln = last_name if last_name is not None else doc.last_name
			updates["seller_name"] = f"{fn} {ln}".strip()
		if business_name is not None:
			updates["business_name"] = business_name
		if phone is not None:
			updates["contact_phone"] = phone
		if country is not None:
			updates["country"] = country
		for field, value in updates.items():
			frappe.db.set_value("Seller Profile", seller_profile, field, value)

	elif seller_app:
		updates = {}
		if business_name is not None:
			updates["business_name"] = business_name
		if phone is not None:
			updates["contact_phone"] = phone
		if country is not None:
			updates["country"] = country
		if address is not None:
			updates["address_line_1"] = address
		if city is not None:
			updates["city"] = city
		for field, value in updates.items():
			frappe.db.set_value("Seller Application", seller_app, field, value)

	else:
		# ── Buyer: update Buyer Profile ──
		buyer_profile = frappe.db.get_value("Buyer Profile", {"user": user}, "name")
		if buyer_profile:
			updates = {}
			if first_name is not None or last_name is not None:
				fn = first_name if first_name is not None else doc.first_name
				ln = last_name if last_name is not None else doc.last_name
				updates["buyer_name"] = f"{fn} {ln}".strip()
			if phone is not None:
				updates["phone"] = phone
			if country is not None:
				updates["country"] = country
			for field, value in updates.items():
				frappe.db.set_value("Buyer Profile", buyer_profile, field, value)

	frappe.db.commit()

	return {"success": True, "message": _("Profile updated successfully.")}
