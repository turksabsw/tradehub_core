import json
import re
import secrets

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import get_url, now_datetime
from frappe.utils.password import check_password, update_password
from tradehub_core.api.v1.auth import _generate_member_id

PASSWORD_MIN_LENGTH = 8
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


# ── Helpers ────────────────────────────────────────────


def _validate_email_format(email: str) -> str:
	"""Return lowered-trimmed email or throw 400."""
	email = (email or "").strip().lower()
	if not _EMAIL_RE.match(email):
		frappe.local.response["http_status_code"] = 400
		frappe.throw(_("Please enter a valid email address."), frappe.ValidationError)
	return email


def _validate_password(password: str):
	"""Enforce password policy: 8+ chars, uppercase, lowercase, digit."""
	if len(password) < PASSWORD_MIN_LENGTH:
		frappe.throw(
			_("Password must be at least {0} characters.").format(PASSWORD_MIN_LENGTH)
		)
	if not re.search(r"[A-Z]", password):
		frappe.throw(_("Password must contain at least one uppercase letter."))
	if not re.search(r"[a-z]", password):
		frappe.throw(_("Password must contain at least one lowercase letter."))
	if not re.search(r"[0-9]", password):
		frappe.throw(_("Password must contain at least one digit."))


def _generate_otp() -> str:
	"""Generate a cryptographically secure 6-digit OTP."""
	return "".join([str(secrets.randbelow(10)) for _ in range(6)])


def _create_email_verification(email: str, first_name: str):
	"""Send a background email verification link after registration."""
	key = frappe.generate_hash(length=32)
	frappe.cache.set_value(
		f"email_verification:{key}", email, expires_in_sec=86400
	)
	link = f"{get_url()}/api/method/tradehub_core.api.v1.identity.verify_email?key={key}"

	frappe.sendmail(
		recipients=email,
		subject="iSTOC — Email Adresinizi Doğrulayın",
		template="tradehub_email_verification",
		args={"link": link, "first_name": first_name},
		now=True,
	)


# ── Endpoints ──────────────────────────────────────────


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(key="email", limit=5, seconds=300)
def send_registration_otp(email: str):
	"""Send a 6-digit OTP to the given email for registration verification.

	Errors:
	  400 — invalid email format
	  409 — email already registered
	  429 — rate limit exceeded
	"""
	email = _validate_email_format(email)

	if frappe.db.exists("User", email):
		frappe.local.response["http_status_code"] = 409
		frappe.throw(
			_("An account with this email already exists."),
			frappe.DuplicateEntryError,
		)

	otp_code = _generate_otp()

	# Store OTP in Redis — overwrites any previous OTP for this email
	frappe.cache.set_value(
		f"registration_otp:{email}",
		json.dumps({"code": otp_code, "attempts": 0}),
		expires_in_sec=600,
	)

	frappe.sendmail(
		recipients=email,
		subject="iSTOC — Kayıt Doğrulama Kodu",
		template="registration_otp",
		args={"code": otp_code},
		now=True,
	)

	return {"success": True, "expires_in_minutes": 10}


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(key="email", limit=5, seconds=300)
def verify_registration_otp(email: str, code: str):
	"""Verify the 6-digit OTP and return a registration_token on success.

	Errors:
	  404 — OTP not found or expired
	  401 — wrong code
	  429 — too many wrong attempts (5+)
	"""
	email = (email or "").strip().lower()
	code = (code or "").strip()

	cache_key = f"registration_otp:{email}"
	cached = frappe.cache.get_value(cache_key)

	if not cached:
		frappe.local.response["http_status_code"] = 404
		frappe.throw(
			_("Verification code not found or expired."),
			frappe.DoesNotExistError,
		)

	otp_data = json.loads(cached) if isinstance(cached, str) else cached

	# Too many wrong attempts — invalidate the OTP
	if otp_data.get("attempts", 0) >= 5:
		frappe.cache.delete_value(cache_key)
		frappe.local.response["http_status_code"] = 429
		frappe.throw(
			_("Too many wrong attempts. Please request a new code."),
			frappe.TooManyRequestsError,
		)

	# Wrong code — increment attempts
	if code != otp_data["code"]:
		otp_data["attempts"] = otp_data.get("attempts", 0) + 1
		frappe.cache.set_value(
			cache_key,
			json.dumps(otp_data),
			expires_in_sec=600,
		)
		frappe.local.response["http_status_code"] = 401
		frappe.throw(
			_("Wrong verification code."),
			frappe.AuthenticationError,
		)

	# Code matches — generate registration_token
	# 60 min TTL to allow time for supplier setup form
	registration_token = frappe.generate_hash(length=32)
	frappe.cache.set_value(
		f"registration_token:{registration_token}",
		email,
		expires_in_sec=3600,
	)

	# Delete OTP (single-use)
	frappe.cache.delete_value(cache_key)

	return {"success": True, "registration_token": registration_token}


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(key="email", limit=5, seconds=3600)
def register_user(
	email: str,
	password: str,
	first_name: str,
	last_name: str,
	account_type: str = "buyer",
	phone: str = "",
	country: str = "Turkey",
	accept_terms: bool = False,
	accept_kvkk: bool = False,
	registration_token: str = "",
):
	"""Register a new user after OTP verification.

	The registration_token must have been obtained from verify_registration_otp().
	"""
	email = _validate_email_format(email)

	# ── Token validation ──
	token_cache_key = f"registration_token:{registration_token}"
	cached_email = frappe.cache.get_value(token_cache_key)

	if not cached_email:
		frappe.throw(
			_(
				"Invalid or expired verification token. "
				"Please restart the registration."
			)
		)

	# Handle bytes from Redis
	if isinstance(cached_email, bytes):
		cached_email = cached_email.decode()

	if cached_email != email:
		frappe.throw(_("Verification token does not match this email."))

	# ── Terms validation ──
	if not accept_terms:
		frappe.throw(_("You must accept the Terms of Service."))
	if not accept_kvkk:
		frappe.throw(_("You must accept the KVKK policy."))

	# ── Password validation ──
	_validate_password(password)

	# ── Duplicate check ──
	if frappe.db.exists("User", email):
		frappe.throw(
			_("An account with this email already exists."),
			frappe.DuplicateEntryError,
		)

	# ── Create User ──
	user = frappe.new_doc("User")
	user.email = email
	user.first_name = first_name
	user.last_name = last_name
	user.send_welcome_email = 0
	user.user_type = "Website User"
	user.flags.ignore_permissions = True
	user.flags.ignore_password_policy = True
	user.insert()

	update_password(email, password)
	user.add_roles("Buyer")

	# ── Generate unique member ID ──
	member_id = _generate_member_id(email, user.creation)

	# ── Create Buyer Profile ──
	buyer = frappe.new_doc("Buyer Profile")
	buyer.user = email
	buyer.buyer_name = f"{first_name} {last_name}"
	buyer.member_id = member_id
	buyer.country = country
	buyer.phone = phone
	buyer.status = "Active"
	buyer.insert(ignore_permissions=True)

	# ── Background email verification ──
	_create_email_verification(email, first_name)

	# ── Delete registration token (single-use) ──
	frappe.cache.delete_value(token_cache_key)

	frappe.db.commit()
	return {
		"success": True,
		"user": email,
		"account_type": account_type,
	}


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(key="email", limit=5, seconds=3600)
def register_supplier(
	email: str,
	password: str,
	first_name: str,
	last_name: str,
	phone: str = "",
	country: str = "Turkey",
	accept_terms: int = 0,
	accept_kvkk: int = 0,
	registration_token: str = "",
	# Supplier form fields
	seller_type: str = "Business",
	business_name: str = "",
	contact_phone: str = "",
	tax_id_type: str = "TCKN",
	tax_id: str = "",
	tax_office: str = "",
	address_line_1: str = "",
	city: str = "",
	bank_name: str = "",
	iban: str = "",
	account_holder_name: str = "",
	identity_document_type: str = "",
	identity_document_number: str = "",
	identity_document_expiry: str = "",
	identity_document: str = "",
	terms_accepted: int = 0,
	privacy_accepted: int = 0,
	kvkk_accepted: int = 0,
	commission_accepted: int = 0,
	return_policy_accepted: int = 0,
):
	"""Register a new supplier in one atomic operation.

	Creates User + Buyer Profile + Seller Application (Submitted) all at once.
	Nothing is written to the database until the full form is submitted.
	"""
	email = _validate_email_format(email)

	# ── Token validation ──
	token_cache_key = f"registration_token:{registration_token}"
	cached_email = frappe.cache.get_value(token_cache_key)

	if not cached_email:
		frappe.throw(
			_(
				"Invalid or expired verification token. "
				"Please restart the registration."
			)
		)

	if isinstance(cached_email, bytes):
		cached_email = cached_email.decode()

	if cached_email != email:
		frappe.throw(_("Verification token does not match this email."))

	# ── Validations ──
	if not accept_terms:
		frappe.throw(_("You must accept the Terms of Service."))
	if not accept_kvkk:
		frappe.throw(_("You must accept the KVKK policy."))
	_validate_password(password)

	if frappe.db.exists("User", email):
		frappe.throw(
			_("An account with this email already exists."),
			frappe.DuplicateEntryError,
		)

	# ── Create User ──
	user = frappe.new_doc("User")
	user.email = email
	user.first_name = first_name
	user.last_name = last_name
	user.send_welcome_email = 0
	user.user_type = "Website User"
	user.flags.ignore_permissions = True
	user.flags.ignore_password_policy = True
	user.insert()

	update_password(email, password)
	user.add_roles("Buyer")

	member_id = _generate_member_id(email, user.creation)

	# ── Create Buyer Profile ──
	buyer = frappe.new_doc("Buyer Profile")
	buyer.user = email
	buyer.buyer_name = f"{first_name} {last_name}"
	buyer.member_id = member_id
	buyer.country = country
	buyer.phone = phone or contact_phone
	buyer.status = "Active"
	buyer.insert(ignore_permissions=True)

	# ── Create Seller Application (Submitted) ──
	app = frappe.new_doc("Seller Application")
	app.applicant_user = email
	app.member_id = member_id
	app.contact_email = email
	app.status = "Submitted"
	app.seller_type = seller_type
	app.business_name = business_name
	app.contact_phone = contact_phone or phone
	app.tax_id_type = tax_id_type
	app.tax_id = tax_id
	app.tax_office = tax_office
	app.address_line_1 = address_line_1
	app.city = city
	app.country = country or "Turkey"
	app.bank_name = bank_name
	app.iban = iban
	app.account_holder_name = account_holder_name
	app.identity_document_type = identity_document_type
	app.identity_document_number = identity_document_number
	app.identity_document_expiry = identity_document_expiry or None
	app.identity_document = identity_document
	app.terms_accepted = int(terms_accepted)
	app.privacy_accepted = int(privacy_accepted)
	app.kvkk_accepted = int(kvkk_accepted)
	app.commission_accepted = int(commission_accepted)
	app.return_policy_accepted = int(return_policy_accepted)
	app.insert(ignore_permissions=True)

	# ── Background email verification ──
	_create_email_verification(email, first_name)

	# ── Delete registration token (single-use) ──
	frappe.cache.delete_value(token_cache_key)

	frappe.db.commit()
	return {
		"success": True,
		"user": email,
		"account_type": "supplier",
		"seller_application": app.name,
		"seller_application_status": app.status,
	}


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(key="email", limit=3, seconds=3600)
def forgot_password(email: str):
	"""Send a password reset link via email.

	Always returns success to prevent email enumeration.
	"""
	email = (email or "").strip().lower()

	# Always return success — email enumeration protection
	if frappe.db.exists("User", email):
		user = frappe.get_doc("User", email)

		# Generate reset key
		reset_key = frappe.generate_hash(length=32)
		user.db_set("reset_password_key", reset_key)
		user.db_set("last_reset_password_key_generated_on", now_datetime())

		# Build reset link pointing to the storefront page
		link = f"{get_url()}/pages/auth/reset-password.html?key={reset_key}"

		frappe.sendmail(
			recipients=email,
			subject="iSTOC — Şifre Sıfırlama",
			template="tradehub_password_reset",
			args={"link": link, "full_name": user.full_name},
			now=True,
		)

	return {
		"success": True,
		"message": _("If this email is registered, a password reset link has been sent."),
	}


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(key="key", limit=5, seconds=3600)
def reset_password(key: str, new_password: str):
	"""Reset password using the key from the email link.

	The key must match User.reset_password_key and be within 24 hours.
	"""
	key = (key or "").strip()

	if not key:
		frappe.throw(
			_("Invalid or expired password reset link."),
			frappe.AuthenticationError,
		)

	# Find user with this reset key
	user_data = frappe.db.get_value(
		"User",
		{"reset_password_key": key},
		["name", "last_reset_password_key_generated_on"],
		as_dict=True,
	)

	if not user_data:
		frappe.throw(
			_("Invalid or expired password reset link."),
			frappe.AuthenticationError,
		)

	# Check 24-hour expiry
	if user_data.last_reset_password_key_generated_on:
		age = (
			now_datetime() - user_data.last_reset_password_key_generated_on
		).total_seconds()
		if age > 86400:
			frappe.throw(
				_("This reset link has expired. Please request a new one."),
				frappe.AuthenticationError,
			)

	# Validate new password
	_validate_password(new_password)

	# Update password and clear reset key
	update_password(user_data.name, new_password, logout_all_sessions=True)
	frappe.db.set_value("User", user_data.name, "reset_password_key", None)

	return {
		"success": True,
		"message": _("Your password has been reset successfully."),
	}


@frappe.whitelist(allow_guest=True, methods=["GET"])
def verify_email(key: str):
	"""Verify user email via the link sent after registration."""
	key = (key or "").strip()

	cache_key = f"email_verification:{key}"
	email = frappe.cache.get_value(cache_key)

	if not email:
		frappe.throw(
			_("Invalid or expired verification link."),
			frappe.AuthenticationError,
		)

	# Handle bytes from Redis
	if isinstance(email, bytes):
		email = email.decode()

	# Mark email as verified on Buyer Profile
	if frappe.db.exists("Buyer Profile", {"user": email}):
		frappe.db.set_value("Buyer Profile", {"user": email}, "email_verified", 1)

	# Delete verification key (single-use)
	frappe.cache.delete_value(cache_key)

	return {"success": True, "message": _("Email verified."), "user": email}


def _verify_password(user: str, password: str):
	"""Verify user password. Raises ValidationError (400) instead of
	AuthenticationError (401) so the frontend api() wrapper does not
	redirect to the login page."""
	try:
		check_password(user, password)
	except frappe.AuthenticationError:
		frappe.local.response["http_status_code"] = 400
		frappe.throw(
			_("Incorrect password."),
			frappe.ValidationError,
		)


@frappe.whitelist(methods=["POST"])
def change_password(current_password: str, new_password: str):
	"""Change password for the currently logged-in user."""
	user = frappe.session.user

	if user == "Guest":
		frappe.throw(_("Not logged in."), frappe.AuthenticationError)

	# Verify current password — returns 400 on failure (not 401)
	_verify_password(user, current_password)

	# Validate new password rules
	_validate_password(new_password)

	# Update password and invalidate all other sessions
	update_password(user, new_password, logout_all_sessions=True)
	frappe.db.commit()

	return {"success": True, "message": _("Password changed successfully.")}


@frappe.whitelist(methods=["POST"])
def change_phone(phone: str, password: str):
	"""Change the phone number for the currently logged-in user.

	Requires the current password for security verification.
	"""
	user = frappe.session.user
	if user == "Guest":
		frappe.throw(_("Not logged in."), frappe.AuthenticationError)

	phone = (phone or "").strip()
	if not phone:
		frappe.local.response["http_status_code"] = 400
		frappe.throw(_("Phone number is required."), frappe.ValidationError)

	# Verify password — returns 400 on failure (not 401)
	_verify_password(user, password)

	# Update User.phone
	frappe.db.set_value("User", user, "phone", phone)

	# Update Buyer Profile if exists
	buyer_profile = frappe.db.get_value("Buyer Profile", {"user": user}, "name")
	if buyer_profile:
		frappe.db.set_value("Buyer Profile", buyer_profile, "phone", phone)

	# Update Seller Profile if exists
	seller_profile = frappe.db.get_value("Seller Profile", {"user": user}, "name")
	if seller_profile:
		frappe.db.set_value("Seller Profile", seller_profile, "contact_phone", phone)

	# Update Seller Application if exists
	seller_app = frappe.db.get_value("Seller Application", {"applicant_user": user}, "name")
	if seller_app:
		frappe.db.set_value("Seller Application", seller_app, "contact_phone", phone)

	frappe.db.commit()

	return {"success": True, "message": _("Phone number updated successfully.")}


@frappe.whitelist(methods=["POST"])
def delete_account(password: str, reason: str = ""):
	"""Soft-delete the currently logged-in user's account.

	Requires the current password for security verification.
	The user is disabled (not physically deleted) so data can be recovered
	within a grace period.

	Session cleanup is handled by the frontend (calls /api/method/logout
	after receiving the success response).
	"""
	user = frappe.session.user
	if user == "Guest":
		frappe.throw(_("Not logged in."), frappe.AuthenticationError)

	# Verify password — returns 400 on failure (not 401)
	_verify_password(user, password)

	# Disable the user (soft-delete)
	frappe.db.set_value("User", user, "enabled", 0)

	# Deactivate Buyer Profile if exists
	buyer_profile = frappe.db.get_value("Buyer Profile", {"user": user}, "name")
	if buyer_profile:
		frappe.db.set_value("Buyer Profile", buyer_profile, "status", "Deactivated")

	# Deactivate Seller Profile if exists
	seller_profile = frappe.db.get_value("Seller Profile", {"user": user}, "name")
	if seller_profile:
		frappe.db.set_value("Seller Profile", seller_profile, "status", "Deactivated")

	# Log the deletion reason
	frappe.log_error(
		title=f"Account deletion: {user}",
		message=f"User {user} requested account deletion.\nReason: {reason or 'Not specified'}",
	)

	frappe.db.commit()

	return {"success": True, "message": _("Your account has been deleted.")}


@frappe.whitelist(methods=["POST"])
def become_seller():
	"""Create a Seller Application for an existing buyer account.

	Returns the application name so the frontend can redirect to the
	supplier setup form.
	"""
	user = frappe.session.user
	if user == "Guest":
		frappe.throw(_("Not logged in."), frappe.AuthenticationError)

	# Already has a seller application
	existing = frappe.db.get_value(
		"Seller Application", {"applicant_user": user}, ["name", "status"], as_dict=True
	)
	if existing:
		return {
			"success": True,
			"seller_application": existing.name,
			"seller_application_status": existing.status,
			"already_exists": True,
		}

	# Generate member_id
	user_data = frappe.db.get_value("User", user, ["email", "creation", "phone"], as_dict=True)
	member_id = (
		frappe.db.get_value("Buyer Profile", {"user": user}, "member_id")
		or _generate_member_id(user_data.email, user_data.creation)
	)

	app = frappe.new_doc("Seller Application")
	app.applicant_user = user
	app.member_id = member_id
	app.contact_email = user
	app.contact_phone = user_data.phone or ""
	app.country = frappe.db.get_value("Buyer Profile", {"user": user}, "country") or "Turkey"
	app.status = "Draft"
	app.insert(ignore_permissions=True)
	frappe.db.commit()

	return {
		"success": True,
		"seller_application": app.name,
		"seller_application_status": app.status,
		"already_exists": False,
	}


@frappe.whitelist(methods=["POST"])
def complete_registration_application(
	seller_application,
	seller_type=None,
	business_name=None,
	contact_phone=None,
	tax_id_type=None,
	tax_id=None,
	tax_office=None,
	address_line_1=None,
	city=None,
	country=None,
	bank_name=None,
	iban=None,
	account_holder_name=None,
	identity_document_type=None,
	identity_document_number=None,
	identity_document_expiry=None,
	identity_document=None,
	terms_accepted=0,
	privacy_accepted=0,
	kvkk_accepted=0,
	commission_accepted=0,
	return_policy_accepted=0,
):
	"""Complete a supplier registration application with business details.

	The caller must be the owner of the Seller Application.
	"""
	user = frappe.session.user
	if user == "Guest":
		frappe.throw(_("Not logged in."), frappe.AuthenticationError)

	# Security: verify ownership
	owner = frappe.db.get_value("Seller Application", seller_application, "applicant_user")
	if not owner or owner != user:
		frappe.throw(
			_("You do not have permission to update this application."),
			frappe.PermissionError,
		)

	doc = frappe.get_doc("Seller Application", seller_application)

	# Assign all fields
	field_map = {
		"seller_type": seller_type,
		"business_name": business_name,
		"contact_phone": contact_phone,
		"tax_id_type": tax_id_type,
		"tax_id": tax_id,
		"tax_office": tax_office,
		"address_line_1": address_line_1,
		"city": city,
		"country": country,
		"bank_name": bank_name,
		"iban": iban,
		"account_holder_name": account_holder_name,
		"identity_document_type": identity_document_type,
		"identity_document_number": identity_document_number,
		"identity_document_expiry": identity_document_expiry,
		"identity_document": identity_document,
		"terms_accepted": int(terms_accepted),
		"privacy_accepted": int(privacy_accepted),
		"kvkk_accepted": int(kvkk_accepted),
		"commission_accepted": int(commission_accepted),
		"return_policy_accepted": int(return_policy_accepted),
	}

	for field, value in field_map.items():
		if value is not None:
			doc.set(field, value)

	doc.status = "Submitted"
	doc.save(ignore_permissions=True)
	frappe.db.commit()

	return {"success": True, "application": doc.name}
