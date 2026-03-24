import frappe
from frappe import _
from frappe.utils import getdate, cint


# Türkçe (DB) → İngilizce (Frontend) status mapping
STATUS_TR_TO_EN = {
    "Ödeme Bekleniyor": "Waiting for payment",
    "Onaylanıyor": "Confirming",
    "Kargoda": "Delivering",
    "Tamamlandı": "Completed",
    "İptal Edildi": "Cancelled",
}

STATUS_COLORS = {
    "Waiting for payment": "text-amber-600",
    "Confirming": "text-blue-600",
    "Delivering": "text-green-600",
    "Completed": "text-gray-500",
    "Cancelled": "text-red-600",
}

STATUS_DESCRIPTIONS = {
    "Waiting for payment": "Please complete your payment.",
    "Confirming": "Your order is being confirmed.",
    "Delivering": "Your order is on its way.",
    "Completed": "Order completed.",
    "Cancelled": "Order cancelled.",
}

# Frontend status key → Türkçe DB değerleri
FILTER_STATUS_MAP = {
    "unpaid": ["Ödeme Bekleniyor"],
    "confirming": ["Onaylanıyor"],
    "preparing": ["Onaylanıyor"],
    "delivering": ["Kargoda"],
    "completed": ["Tamamlandı"],
    "cancelled": ["İptal Edildi"],
    "refunds-aftersales": ["İptal Edildi"],
    "closed": ["Tamamlandı", "İptal Edildi"],
}


def _require_buyer():
    """Ensure user is logged in and return user email."""
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw(_("You must be logged in"), frappe.AuthenticationError)
    return user


def _translate_order(order):
    """Order dict'indeki Türkçe status'u İngilizce'ye çevir ve eksik alanları ekle."""
    tr_status = order.get("status", "")
    en_status = STATUS_TR_TO_EN.get(tr_status, tr_status)

    seller_name = ""
    seller_id = order.get("seller")
    if seller_id:
        seller_name = frappe.db.get_value("Admin Seller Profile", seller_id, "seller_name") or seller_id

    order["order_number"] = order.get("name", "")
    order["seller_name"] = seller_name
    order["status"] = en_status
    order["status_color"] = STATUS_COLORS.get(en_status, "text-gray-500")
    order["status_description"] = STATUS_DESCRIPTIONS.get(en_status, "")
    order["grand_total"] = float(order.get("total") or 0)
    refund_status = order.get("refund_status") or ""
    if refund_status == "Approved":
        order["payment_status"] = "Refunded"
    elif en_status == "Waiting for payment":
        order["payment_status"] = "Unpaid"
    else:
        order["payment_status"] = "Paid"
    order["shipping_status"] = "Pending" if en_status in ("Waiting for payment", "Confirming") else "In Transit"
    order["supplier_name"] = seller_name
    order["supplier_contact"] = ""
    order["supplier_phone"] = ""
    order["supplier_email"] = ""
    order["incoterms"] = "DAP"

    return order


@frappe.whitelist()
def get_my_orders(status=None, search=None, date_from=None, date_to=None, page=1, page_size=20):
    """
    List buyer's orders with filtering.
    Reads from Order doctype (Türkçe status), translates to English for frontend.
    """
    buyer = _require_buyer()
    page = cint(page) or 1
    page_size = min(cint(page_size) or 20, 100)

    filters = {"buyer": buyer}

    if status and status != "all":
        tr_statuses = FILTER_STATUS_MAP.get(status)
        if tr_statuses:
            filters["status"] = ["in", tr_statuses]

    if date_from and date_to:
        filters["order_date"] = ["between", [getdate(date_from), getdate(date_to)]]
    elif date_from:
        filters["order_date"] = [">=", getdate(date_from)]
    elif date_to:
        filters["order_date"] = ["<=", getdate(date_to)]

    total = frappe.db.count("Order", filters=filters)

    orders = frappe.get_list(
        "Order",
        filters=filters,
        fields=[
            "name", "order_date", "seller", "status",
            "currency", "payment_method",
            "subtotal", "shipping_fee", "total",
            "shipping_address", "ship_from", "shipping_method",
            "remittance_amount",
            "refund_status", "refund_reason", "refund_amount", "refund_requested_at",
        ],
        order_by="order_date desc",
        start=(page - 1) * page_size,
        page_length=page_size,
        ignore_permissions=True,
    )

    if search:
        q = search.lower()
        filtered = []
        for o in orders:
            name_match = q in (o.get("name") or "").lower()
            seller_id = o.get("seller")
            seller_name = ""
            if seller_id:
                seller_name = frappe.db.get_value("Admin Seller Profile", seller_id, "seller_name") or ""
            seller_match = q in seller_name.lower()
            if name_match or seller_match:
                filtered.append(o)
        orders = filtered

    for order in orders:
        _translate_order(order)
        order["items"] = frappe.get_all(
            "Order Item",
            filters={"parent": order["name"]},
            fields=["listing_title as product_name", "variation", "unit_price", "quantity", "total_price", "image"],
            order_by="idx asc",
        )

    return {
        "success": True,
        "orders": orders,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@frappe.whitelist()
def get_order_detail(order_number):
    """Get single order detail."""
    buyer = _require_buyer()

    order = frappe.db.get_value(
        "Order",
        {"name": order_number, "buyer": buyer},
        "*",
        as_dict=True,
    )

    if not order:
        frappe.throw(_("Order not found"), frappe.DoesNotExistError)

    _translate_order(order)
    order["items"] = frappe.get_all(
        "Order Item",
        filters={"parent": order["name"]},
        fields=["listing_title as product_name", "variation", "unit_price", "quantity", "total_price", "image"],
        order_by="idx asc",
    )

    return {"success": True, "order": order}


@frappe.whitelist()
def cancel_order(order_number, reason=None):
    """Cancel an order."""
    buyer = _require_buyer()

    if not frappe.db.exists("Order", {"name": order_number, "buyer": buyer}):
        frappe.throw(_("Order not found"), frappe.DoesNotExistError)

    order = frappe.get_doc("Order", order_number)

    if order.status in ("Tamamlandı", "İptal Edildi"):
        en_status = STATUS_TR_TO_EN.get(order.status, order.status)
        frappe.throw(_("Cannot cancel an order with status: {0}").format(en_status))

    order.status = "İptal Edildi"
    order.save(ignore_permissions=True)
    frappe.db.commit()

    return {
        "success": True,
        "order_number": order.name,
        "status": "Cancelled",
    }


@frappe.whitelist()
def get_order_counts():
    """Get order counts by status for tab badges."""
    buyer = _require_buyer()

    counts = frappe.db.sql("""
        SELECT status, COUNT(*) as count
        FROM `tabOrder`
        WHERE buyer = %(buyer)s
        GROUP BY status
    """, {"buyer": buyer}, as_dict=True)

    result = {}
    total = 0
    for row in counts:
        en_status = STATUS_TR_TO_EN.get(row["status"], row["status"])
        result[en_status] = row["count"]
        total += row["count"]

    result["all"] = total

    return {"success": True, "counts": result}


@frappe.whitelist()
def get_payment_records(order_number):
    """Get payment records for an order. Returns remittance data if available."""
    buyer = _require_buyer()

    order = frappe.db.get_value(
        "Order", {"name": order_number, "buyer": buyer},
        ["remittance_date", "remittance_amount", "remittance_sender",
         "receipt_url", "currency", "status",
         "refund_status", "refund_reason", "refund_amount", "refund_requested_at"],
        as_dict=True,
    )

    if not order:
        frappe.throw(_("Order not found"), frappe.DoesNotExistError)

    payments = []
    wire_transfers = []

    if order.remittance_date or order.remittance_amount:
        en_status = STATUS_TR_TO_EN.get(order.status, order.status)
        payment_status = "Pending" if en_status == "Waiting for payment" else "Completed"

        payments.append({
            "name": order_number,
            "payment_date": str(order.remittance_date) if order.remittance_date else None,
            "method": "Havale/EFT",
            "amount": float(order.remittance_amount or 0),
            "currency": order.currency or "USD",
            "status": payment_status,
        })

        wire_transfers.append({
            "name": order_number,
            "payment_date": str(order.remittance_date) if order.remittance_date else None,
            "reference": order.remittance_sender or "",
            "amount": float(order.remittance_amount or 0),
            "currency": order.currency or "USD",
            "status": payment_status,
            "receipt_url": order.receipt_url or "",
        })

    refunds = []
    if order.refund_status:
        refund_status_label = {
            "Pending": "Beklemede",
            "Approved": "Onaylandı",
            "Rejected": "Reddedildi",
        }.get(order.refund_status, order.refund_status)
        refunds.append({
            "name": order_number,
            "payment_date": str(order.refund_requested_at)[:10] if order.refund_requested_at else None,
            "reason": order.refund_reason or "",
            "amount": float(order.refund_amount or 0),
            "currency": order.currency or "USD",
            "status": refund_status_label,
        })

    return {
        "success": True,
        "payments": payments,
        "refunds": refunds,
        "wire_transfers": wire_transfers,
    }


@frappe.whitelist()
def get_seller_bank_info(order_number):
    """Returns seller's IBAN and bank info for the given order."""
    buyer = _require_buyer()

    order = frappe.db.get_value(
        "Order", {"name": order_number, "buyer": buyer},
        ["seller"], as_dict=True
    )
    if not order:
        frappe.throw(_("Order not found"), frappe.DoesNotExistError)

    seller_code = order.seller
    # Fallback: if seller not set on order, derive it from the first listing item
    if not seller_code:
        first_listing = frappe.db.get_value("Order Item", {"parent": order_number}, "listing")
        if first_listing:
            seller_code = frappe.db.get_value("Listing", first_listing, "seller_profile")

    seller_info = frappe.db.get_value(
        "Admin Seller Profile", seller_code,
        ["bank_name", "iban", "account_holder", "seller_name"],
        as_dict=True
    )
    if not seller_info:
        return {"iban": "", "bank_name": "", "account_holder": "", "seller_name": ""}

    return {
        "iban": seller_info.iban or "",
        "bank_name": seller_info.bank_name or "",
        "account_holder": seller_info.account_holder or "",
        "seller_name": seller_info.seller_name or "",
    }


@frappe.whitelist()
def get_my_refunds():
    """Returns buyer's orders that have a refund request (any status)."""
    buyer = _require_buyer()
    orders = frappe.get_list(
        "Order",
        filters={"buyer": buyer, "refund_status": ["!=", ""]},
        fields=["name", "order_date", "seller", "status", "currency",
                "total", "refund_status", "refund_reason", "refund_amount", "refund_requested_at"],
        order_by="refund_requested_at desc",
        ignore_permissions=True,
    )
    result = []
    for o in orders:
        seller_name = ""
        if o.seller:
            seller_name = frappe.db.get_value("Admin Seller Profile", o.seller, "seller_name") or ""
        status_label = {
            "Pending": "Beklemede",
            "Approved": "Onaylandı",
            "Rejected": "Reddedildi",
        }.get(o.refund_status or "", o.refund_status or "")
        result.append({
            "order_number": o.name,
            "order_date": str(o.order_date)[:10] if o.order_date else "",
            "seller_name": seller_name,
            "currency": o.currency or "USD",
            "order_total": float(o.total or 0),
            "refund_status": o.refund_status or "",
            "refund_status_label": status_label,
            "refund_reason": o.refund_reason or "",
            "refund_amount": float(o.refund_amount or 0),
            "refund_requested_at": str(o.refund_requested_at)[:10] if o.refund_requested_at else "",
        })
    return {"success": True, "refunds": result}


@frappe.whitelist()
def upload_receipt(order_number, file_name, file_data):
    """Upload payment receipt file for an order. file_data must be base64-encoded."""
    import base64
    buyer = _require_buyer()

    if not frappe.db.exists("Order", {"name": order_number, "buyer": buyer}):
        frappe.throw(_("Order not found"), frappe.DoesNotExistError)

    content = base64.b64decode(file_data)

    file_doc = frappe.get_doc({
        "doctype": "File",
        "file_name": file_name,
        "attached_to_doctype": "Order",
        "attached_to_name": order_number,
        "attached_to_field": "receipt_url",
        "content": content,
        "is_private": 0,
    })
    file_doc.flags.ignore_permissions = True
    file_doc.insert(ignore_permissions=True)

    frappe.db.set_value("Order", order_number, "receipt_url", file_doc.file_url)
    frappe.db.commit()

    return {"file_url": file_doc.file_url}


@frappe.whitelist()
def submit_remittance(order_number, remittance_date, currency="USD", amount=0,
                      bank_name="", sender_name="", receipt_url="", beneficiary_account=""):
    """Buyer submits bank transfer proof — saves remittance details on order."""
    buyer = _require_buyer()

    if not frappe.db.exists("Order", {"name": order_number, "buyer": buyer}):
        frappe.throw(_("Order not found"), frappe.DoesNotExistError)

    order = frappe.get_doc("Order", order_number)
    frappe.db.set_value("Order", order.name, {
        "remittance_date": remittance_date or None,
        "remittance_amount": float(amount or 0),
        "remittance_sender": sender_name or "",
        "receipt_url": receipt_url or "",
    })
    frappe.db.commit()

    return {
        "success": True,
        "order_number": order.name,
        "order_status": STATUS_TR_TO_EN.get(order.status, order.status),
    }


@frappe.whitelist()
def get_seller_orders(status=None, page=1, page_size=20):
    """Returns orders for the logged-in seller."""
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw(_("Authentication required"), frappe.AuthenticationError)

    seller_code = frappe.db.get_value("Admin Seller Profile", {"user": user}, "name")
    if not seller_code:
        frappe.throw(_("Seller profile not found"))

    page = cint(page) or 1
    page_size = min(cint(page_size) or 20, 100)

    filters = {"seller": seller_code}
    if status and status != "all":
        tr_statuses = FILTER_STATUS_MAP.get(status)
        if tr_statuses:
            filters["status"] = ["in", tr_statuses]

    total = frappe.db.count("Order", filters=filters)

    orders = frappe.get_list(
        "Order",
        filters=filters,
        fields=["name", "order_date", "buyer", "status", "currency",
                "payment_method", "subtotal", "shipping_fee", "total",
                "receipt_url", "remittance_date", "remittance_sender", "remittance_amount",
                "refund_status", "refund_reason", "refund_amount", "refund_requested_at"],
        order_by="order_date desc",
        start=(page - 1) * page_size,
        page_length=page_size,
        ignore_permissions=True,
    )

    for order in orders:
        tr_status = order.get("status", "")
        en_status = STATUS_TR_TO_EN.get(tr_status, tr_status)
        order["order_number"] = order.get("name", "")
        order["status_en"] = en_status
        order["status_color"] = STATUS_COLORS.get(en_status, "text-gray-500")
        order["buyer_name"] = frappe.db.get_value(
            "User", order.get("buyer"), "full_name"
        ) or order.get("buyer", "")
        order["items"] = frappe.get_all(
            "Order Item",
            filters={"parent": order["name"]},
            fields=["listing_title as product_name", "quantity", "unit_price", "total_price"],
            order_by="idx asc",
        )

    return {"success": True, "orders": orders, "total": total, "page": page, "page_size": page_size}


@frappe.whitelist()
def seller_confirm_payment(order_number):
    """Seller confirms payment received — changes order status to 'Onaylanıyor'."""
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw(_("Authentication required"), frappe.AuthenticationError)

    seller_code = frappe.db.get_value("Admin Seller Profile", {"user": user}, "name")
    if not seller_code:
        frappe.throw(_("Seller profile not found"))

    order = frappe.db.get_value(
        "Order", {"name": order_number, "seller": seller_code},
        ["name", "status"], as_dict=True
    )
    if not order:
        frappe.throw(_("Order not found"), frappe.DoesNotExistError)

    if order.status != "Ödeme Bekleniyor":
        frappe.throw(_("Payment can only be confirmed for orders awaiting payment"))

    frappe.db.set_value("Order", order.name, "status", "Onaylanıyor")
    frappe.db.commit()

    return {"success": True, "order_number": order_number}


def _generate_invoice_html(order, items, seller_name, buyer_name):
    """Generate a clean HTML invoice for an order."""
    from frappe.utils import format_date
    order_date = format_date(order.order_date) if order.order_date else ""
    currency = order.currency or "USD"

    rows = ""
    for item in items:
        rows += f"""
        <tr>
          <td style="padding:10px 12px;border-bottom:1px solid #f1f5f9;">{item.listing_title or ""}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #f1f5f9;color:#64748b;">{item.variation or ""}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #f1f5f9;text-align:right;">{currency} {float(item.unit_price or 0):,.2f}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #f1f5f9;text-align:center;">{int(item.quantity or 1)}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #f1f5f9;text-align:right;font-weight:600;">{currency} {float(item.total_price or 0):,.2f}</td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="tr">
<head>
  <meta charset="UTF-8">
  <title>Fatura — {order.name}</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#1e293b;background:#f8fafc;padding:40px 20px}}
    .invoice{{max-width:780px;margin:0 auto;background:#fff;border-radius:12px;box-shadow:0 1px 6px rgba(0,0,0,.08);overflow:hidden}}
    .header{{background:linear-gradient(135deg,#7c3aed,#4f46e5);color:#fff;padding:32px 40px;display:flex;justify-content:space-between;align-items:center}}
    .header h1{{font-size:26px;font-weight:700;letter-spacing:-.5px}}
    .header .inv-num{{font-size:13px;opacity:.85;margin-top:4px}}
    .badge{{background:rgba(255,255,255,.2);border-radius:20px;padding:4px 14px;font-size:12px;font-weight:600}}
    .body{{padding:36px 40px}}
    .meta-grid{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:24px;margin-bottom:32px}}
    .meta-card{{background:#f8fafc;border-radius:8px;padding:16px}}
    .meta-card .lbl{{font-size:11px;font-weight:600;color:#94a3b8;text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px}}
    .meta-card .val{{font-size:14px;color:#1e293b;font-weight:500}}
    table{{width:100%;border-collapse:collapse;margin-bottom:24px}}
    thead tr{{background:#f8fafc}}
    thead th{{padding:10px 12px;text-align:left;font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.5px;border-bottom:2px solid #e2e8f0}}
    thead th:nth-child(3),thead th:nth-child(4),thead th:nth-child(5){{text-align:right}}
    thead th:nth-child(4){{text-align:center}}
    .totals{{margin-left:auto;width:280px}}
    .totals .row{{display:flex;justify-content:space-between;padding:7px 0;font-size:14px;color:#475569;border-bottom:1px solid #f1f5f9}}
    .totals .row.total{{font-size:16px;font-weight:700;color:#1e293b;border-bottom:none;padding-top:12px;margin-top:4px}}
    .footer{{text-align:center;padding:20px 40px;background:#f8fafc;border-top:1px solid #e2e8f0;font-size:12px;color:#94a3b8}}
    @media print{{body{{background:#fff;padding:0}}.invoice{{box-shadow:none;border-radius:0}}}}
  </style>
</head>
<body>
  <div class="invoice">
    <div class="header">
      <div>
        <h1>İSTOÇ TradeHub</h1>
        <div class="inv-num">Sipariş Faturası</div>
      </div>
      <div style="text-align:right">
        <div style="font-size:22px;font-weight:700">{order.name}</div>
        <div class="badge" style="margin-top:8px">Fatura</div>
      </div>
    </div>
    <div class="body">
      <div class="meta-grid">
        <div class="meta-card">
          <div class="lbl">Sipariş Tarihi</div>
          <div class="val">{order_date}</div>
        </div>
        <div class="meta-card">
          <div class="lbl">Satıcı</div>
          <div class="val">{seller_name}</div>
        </div>
        <div class="meta-card">
          <div class="lbl">Alıcı</div>
          <div class="val">{buyer_name}</div>
        </div>
      </div>

      <table>
        <thead>
          <tr>
            <th>Ürün</th>
            <th>Özellik</th>
            <th style="text-align:right">Birim Fiyat</th>
            <th style="text-align:center">Adet</th>
            <th style="text-align:right">Toplam</th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>

      <div class="totals">
        <div class="row"><span>Ara toplam</span><span>{currency} {float(order.subtotal or 0):,.2f}</span></div>
        <div class="row"><span>Kargo</span><span>{currency} {float(order.shipping_fee or 0):,.2f}</span></div>
        {'<div class="row"><span>Kupon indirimi</span><span>-' + currency + ' ' + f"{float(order.coupon_discount or 0):,.2f}" + '</span></div>' if float(order.coupon_discount or 0) > 0 else ''}
        <div class="row total"><span>Genel Toplam</span><span>{currency} {float(order.total or 0):,.2f}</span></div>
      </div>
    </div>
    <div class="footer">Bu belge İSTOÇ TradeHub platformu tarafından otomatik olarak oluşturulmuştur.</div>
  </div>
  <script>window.onload = function(){{ window.print(); }};</script>
</body>
</html>"""


@frappe.whitelist()
def download_invoice(order_number):
    """Generate and return HTML invoice for an order."""
    buyer = _require_buyer()

    order = frappe.db.get_value(
        "Order", {"name": order_number, "buyer": buyer}, "*", as_dict=True
    )
    if not order:
        frappe.throw(_("Order not found"), frappe.DoesNotExistError)

    items = frappe.get_all(
        "Order Item",
        filters={"parent": order_number},
        fields=["listing_title", "variation", "unit_price", "quantity", "total_price"],
        order_by="idx asc",
    )

    seller_name = ""
    if order.seller:
        seller_name = frappe.db.get_value("Admin Seller Profile", order.seller, "seller_name") or ""
    buyer_name = frappe.db.get_value("User", order.buyer, "full_name") or order.buyer

    html = _generate_invoice_html(order, items, seller_name, buyer_name)
    return {"html": html, "filename": f"Fatura-{order_number}.html"}


@frappe.whitelist()
def submit_refund_request(order_number, reason, amount=0):
    """Buyer submits a refund request — saved on the order for seller to review."""
    buyer = _require_buyer()

    order = frappe.db.get_value(
        "Order", {"name": order_number, "buyer": buyer},
        ["name", "status", "refund_status"], as_dict=True
    )
    if not order:
        frappe.throw(_("Order not found"), frappe.DoesNotExistError)

    if order.refund_status in ("Pending", "Approved"):
        frappe.throw(_("Bu sipariş için zaten bir iade talebi mevcut"))

    frappe.db.set_value("Order", order_number, {
        "refund_status": "Pending",
        "refund_reason": reason or "",
        "refund_amount": float(amount or 0),
        "refund_requested_at": frappe.utils.now_datetime(),
    })
    frappe.db.commit()
    return {"success": True}


@frappe.whitelist()
def seller_handle_refund(order_number, action):
    """Seller approves or rejects a refund request. action: 'approve' or 'reject'"""
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw(_("Authentication required"), frappe.AuthenticationError)

    seller_code = frappe.db.get_value("Admin Seller Profile", {"user": user}, "name")
    if not seller_code:
        frappe.throw(_("Seller profile not found"))

    order = frappe.db.get_value(
        "Order", {"name": order_number, "seller": seller_code},
        ["name", "refund_status"], as_dict=True
    )
    if not order:
        frappe.throw(_("Order not found"), frappe.DoesNotExistError)

    if order.refund_status != "Pending":
        frappe.throw(_("No pending refund request for this order"))

    new_status = "Approved" if action == "approve" else "Rejected"
    frappe.db.set_value("Order", order_number, "refund_status", new_status)
    frappe.db.commit()
    return {"success": True, "refund_status": new_status}
