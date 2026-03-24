# Copyright (c) 2026, TR TradeHub and contributors
# For license information, please see license.txt

"""
Seller Payout Utility for TR-TradeHub Marketplace

This module handles the connection between order completion and seller balance updates.
It manages:
- Crediting seller balance when orders are completed
- Commission calculation and deduction
- Escrow release to available balance
- Payout scheduling integration
"""

import frappe
from frappe import _
from frappe.utils import flt, cint, now_datetime, add_days, nowdate
from typing import Dict, Optional

from tradehub_commerce.tradehub_commerce.utils.commission_utils import (
    is_commission_enabled,
    get_zero_commission_result,
)


# =================================================================
# Order Completion to Balance Credit
# =================================================================

def credit_seller_for_order(
    seller: str,
    sub_order: str,
    amount: float,
    commission_amount: float = 0,
    release_immediately: bool = False
) -> Dict:
    """
    Credit seller balance when an order is completed.

    This function:
    1. Gets or creates the seller balance record
    2. Adds earnings to pending balance
    3. Deducts commission
    4. Optionally releases to available balance immediately

    Args:
        seller: Seller Profile name
        sub_order: Sub Order name for reference
        amount: Seller payout amount (after commission)
        commission_amount: Commission deducted
        release_immediately: If True, release directly to available balance

    Returns:
        dict: Balance update result
    """
    from tradehub_seller.tradehub_seller.doctype.seller_balance.seller_balance import get_or_create_seller_balance

    balance = get_or_create_seller_balance(seller)

    # Calculate gross earnings (payout + commission)
    gross_amount = flt(amount) + flt(commission_amount)

    # Add earnings to pending balance
    balance.add_earnings(
        amount=gross_amount,
        order_name=sub_order,
        description=f"Order completed: {sub_order}"
    )

    # Deduct commission
    if flt(commission_amount) > 0:
        balance.deduct_commission(
            amount=commission_amount,
            order_name=sub_order,
            description=f"Platform commission for order {sub_order}"
        )

    # Record order completion statistics
    balance.record_order_completion(
        order_value=gross_amount,
        commission_amount=commission_amount
    )

    # Release to available if requested (immediate payout)
    if release_immediately:
        balance.release_to_available(
            amount=flt(amount),
            escrow_name=sub_order,
            description=f"Immediate release for order {sub_order}"
        )

    frappe.db.commit()

    return {
        "status": "success",
        "seller": seller,
        "sub_order": sub_order,
        "gross_amount": gross_amount,
        "commission": commission_amount,
        "net_amount": amount,
        "released_immediately": release_immediately,
        "pending_balance": balance.pending_balance,
        "available_balance": balance.available_balance
    }


def release_escrow_to_balance(
    seller: str,
    sub_order: str,
    amount: float,
    escrow_name: str = None
) -> Dict:
    """
    Release escrow funds to seller's available balance.

    Called when escrow period ends or buyer confirms delivery.

    Args:
        seller: Seller Profile name
        sub_order: Sub Order name
        amount: Amount to release
        escrow_name: Escrow Account name if applicable

    Returns:
        dict: Release result
    """
    from tradehub_seller.tradehub_seller.doctype.seller_balance.seller_balance import get_or_create_seller_balance

    balance = get_or_create_seller_balance(seller)

    balance.release_to_available(
        amount=flt(amount),
        escrow_name=escrow_name or sub_order,
        description=f"Escrow released for order {sub_order}"
    )

    frappe.db.commit()

    return {
        "status": "success",
        "seller": seller,
        "sub_order": sub_order,
        "amount_released": amount,
        "pending_balance": balance.pending_balance,
        "available_balance": balance.available_balance
    }


def process_order_refund_from_balance(
    seller: str,
    sub_order: str,
    refund_amount: float,
    commission_refund: float = 0
) -> Dict:
    """
    Process refund deduction from seller balance.

    Called when an order is refunded after seller has been credited.

    Args:
        seller: Seller Profile name
        sub_order: Sub Order name
        refund_amount: Amount to refund from seller
        commission_refund: Commission amount to return to seller

    Returns:
        dict: Refund processing result
    """
    from tradehub_seller.tradehub_seller.doctype.seller_balance.seller_balance import get_or_create_seller_balance

    balance = get_or_create_seller_balance(seller)

    # Deduct refund amount
    net_deduction = flt(refund_amount) - flt(commission_refund)

    if net_deduction > 0:
        balance.process_refund(
            amount=net_deduction,
            order_name=sub_order,
            description=f"Refund processed for order {sub_order}"
        )

    frappe.db.commit()

    return {
        "status": "success",
        "seller": seller,
        "sub_order": sub_order,
        "refund_amount": refund_amount,
        "commission_refunded": commission_refund,
        "net_deduction": net_deduction,
        "available_balance": balance.available_balance,
        "pending_balance": balance.pending_balance
    }


# =================================================================
# Commission Calculation
# =================================================================

def calculate_commission(
    seller: str,
    order_total: float,
    category: str = None,
    use_seller_plan: bool = True
) -> Dict:
    """
    Calculate commission for an order.

    Args:
        seller: Seller Profile name
        order_total: Total order amount
        category: Product category for category-specific rates
        use_seller_plan: Use seller's commission plan if available

    Returns:
        dict: Commission calculation result
    """
    if not is_commission_enabled():
        return get_zero_commission_result(order_total)

    commission_rate = 0
    commission_plan = None

    if use_seller_plan:
        # Get seller's commission plan
        seller_doc = frappe.db.get_value(
            "Seller Profile",
            seller,
            ["commission_plan", "custom_commission_rate"],
            as_dict=True
        )

        if seller_doc and seller_doc.commission_plan:
            commission_plan = seller_doc.commission_plan
            plan_data = frappe.db.get_value(
                "Commission Plan",
                commission_plan,
                ["base_commission_rate", "enable_category_rates"],
                as_dict=True
            )

            if plan_data:
                commission_rate = flt(plan_data.base_commission_rate)

                # Check for category-specific rate
                if plan_data.enable_category_rates and category:
                    category_rate = frappe.db.get_value(
                        "Commission Plan Rate",
                        {"parent": commission_plan, "category": category},
                        "commission_rate"
                    )
                    if category_rate is not None:
                        commission_rate = flt(category_rate)

        elif seller_doc and seller_doc.custom_commission_rate:
            commission_rate = flt(seller_doc.custom_commission_rate)

    # If no seller plan, check for default plan
    if not commission_plan:
        default_plan = frappe.db.get_value(
            "Commission Plan",
            {"is_default": 1, "status": "Active"},
            ["name", "base_commission_rate"],
            as_dict=True
        )
        if default_plan:
            commission_plan = default_plan.name
            commission_rate = flt(default_plan.base_commission_rate)

    # Calculate commission amount
    commission_amount = flt(order_total) * flt(commission_rate) / 100
    seller_payout = flt(order_total) - commission_amount

    return {
        "commission_plan": commission_plan,
        "commission_rate": commission_rate,
        "order_total": order_total,
        "commission_amount": round(commission_amount, 2),
        "seller_payout": round(seller_payout, 2)
    }


# =================================================================
# Escrow and Payout Hold Management
# =================================================================

def get_payout_hold_days(seller: str) -> int:
    """
    Get the payout hold days for a seller based on their commission plan.

    Args:
        seller: Seller Profile name

    Returns:
        int: Number of days to hold payout
    """
    # Check seller's commission plan
    commission_plan = frappe.db.get_value("Seller Profile", seller, "commission_plan")

    if commission_plan:
        hold_days = frappe.db.get_value("Commission Plan", commission_plan, "payout_hold_days")
        if hold_days is not None:
            return cint(hold_days)

    # Default hold days
    return 14


def schedule_escrow_release(
    seller: str,
    sub_order: str,
    amount: float,
    hold_days: int = None
) -> Dict:
    """
    Schedule escrow release for a completed order.

    Args:
        seller: Seller Profile name
        sub_order: Sub Order name
        amount: Amount to release
        hold_days: Days to hold before release (uses seller plan default if not specified)

    Returns:
        dict: Scheduling result
    """
    if hold_days is None:
        hold_days = get_payout_hold_days(seller)

    release_date = add_days(nowdate(), hold_days)

    # Create or update escrow release schedule
    # This could be stored in a separate table or as a scheduled task

    frappe.publish_realtime(
        "escrow_release_scheduled",
        {
            "seller": seller,
            "sub_order": sub_order,
            "amount": amount,
            "release_date": str(release_date)
        },
        doctype="Sub Order",
        docname=sub_order
    )

    return {
        "status": "scheduled",
        "seller": seller,
        "sub_order": sub_order,
        "amount": amount,
        "hold_days": hold_days,
        "release_date": str(release_date)
    }


def process_escrow_release(sub_order: str) -> Dict:
    """
    Process escrow release for a sub order.

    Args:
        sub_order: Sub Order name

    Returns:
        dict: Processing result
    """
    sub_order_doc = frappe.get_doc("Sub Order", sub_order)

    if sub_order_doc.payout_status == "Paid":
        return {"status": "already_processed", "message": "Payout already processed"}

    result = release_escrow_to_balance(
        seller=sub_order_doc.seller,
        sub_order=sub_order,
        amount=flt(sub_order_doc.seller_payout)
    )

    # Update sub order payout status
    sub_order_doc.db_set("payout_status", "Released")

    return result


# =================================================================
# Batch Processing
# =================================================================

def process_pending_escrow_releases():
    """
    Process all pending escrow releases that are due.
    Called by scheduled job.

    Returns:
        dict: Processing results
    """
    today = nowdate()

    # Find sub orders with escrow ready for release
    # Criteria: Delivered/Completed status, escrow hold period passed, not yet released
    sub_orders = frappe.db.sql("""
        SELECT name, seller, seller_payout, delivered_at
        FROM `tabSub Order`
        WHERE status IN ('Delivered', 'Completed')
        AND payout_status IN ('Pending', 'Scheduled')
        AND escrow_status = 'Held'
        AND DATEDIFF(%(today)s, DATE(delivered_at)) >= 14
    """, {"today": today}, as_dict=True)

    processed = 0
    failed = 0
    errors = []

    for so in sub_orders:
        try:
            result = process_escrow_release(so.name)
            if result.get("status") == "success":
                processed += 1
            else:
                failed += 1
                errors.append(f"{so.name}: {result.get('message', 'Unknown error')}")
        except Exception as e:
            failed += 1
            errors.append(f"{so.name}: {str(e)}")
            frappe.log_error(
                f"Failed to process escrow release for {so.name}: {str(e)}",
                "Escrow Release Error"
            )

    return {
        "status": "completed",
        "processed": processed,
        "failed": failed,
        "errors": errors
    }


# =================================================================
# Sub Order Integration Methods
# =================================================================

def on_sub_order_completed(sub_order_name: str) -> Dict:
    """
    Handle sub order completion - credit seller balance.

    This is the main integration point called when a Sub Order
    transitions to 'Completed' status.

    Args:
        sub_order_name: Sub Order name

    Returns:
        dict: Processing result
    """
    sub_order = frappe.get_doc("Sub Order", sub_order_name)

    # Skip if already processed
    if sub_order.payout_status == "Paid":
        return {"status": "skipped", "reason": "Already paid"}

    # Credit seller balance
    result = credit_seller_for_order(
        seller=sub_order.seller,
        sub_order=sub_order_name,
        amount=flt(sub_order.seller_payout),
        commission_amount=flt(sub_order.commission_amount),
        release_immediately=False  # Go through escrow
    )

    # Schedule escrow release
    schedule_result = schedule_escrow_release(
        seller=sub_order.seller,
        sub_order=sub_order_name,
        amount=flt(sub_order.seller_payout)
    )

    # Update sub order
    sub_order.db_set("payout_status", "Scheduled")

    return {
        "credit_result": result,
        "escrow_schedule": schedule_result
    }


def on_sub_order_refunded(sub_order_name: str, refund_amount: float = None) -> Dict:
    """
    Handle sub order refund - debit seller balance.

    Args:
        sub_order_name: Sub Order name
        refund_amount: Override refund amount (uses sub_order.refund_amount if not provided)

    Returns:
        dict: Processing result
    """
    sub_order = frappe.get_doc("Sub Order", sub_order_name)

    if not refund_amount:
        refund_amount = flt(sub_order.refund_amount)

    # Calculate commission portion to return
    commission_rate = 0
    if flt(sub_order.grand_total) > 0:
        commission_rate = flt(sub_order.commission_amount) / flt(sub_order.grand_total)

    commission_refund = refund_amount * commission_rate

    result = process_order_refund_from_balance(
        seller=sub_order.seller,
        sub_order=sub_order_name,
        refund_amount=refund_amount,
        commission_refund=commission_refund
    )

    return result


# =================================================================
# API Endpoints
# =================================================================

@frappe.whitelist()
def get_seller_payout_info(seller: str) -> Dict:
    """
    Get seller payout information including pending orders.

    Args:
        seller: Seller Profile name

    Returns:
        dict: Payout information
    """
    from tradehub_seller.tradehub_seller.doctype.seller_balance.seller_balance import get_or_create_seller_balance

    balance = get_or_create_seller_balance(seller)

    # Get pending payouts from sub orders
    pending_orders = frappe.db.sql("""
        SELECT name, sub_order_id, seller_payout, delivered_at, payout_status
        FROM `tabSub Order`
        WHERE seller = %(seller)s
        AND payout_status IN ('Pending', 'Scheduled')
        ORDER BY delivered_at DESC
        LIMIT 10
    """, {"seller": seller}, as_dict=True)

    return {
        "balance": balance.get_balance_summary(),
        "pending_orders": pending_orders,
        "hold_days": get_payout_hold_days(seller)
    }


@frappe.whitelist()
def calculate_order_commission(seller: str, order_total: float, category: str = None) -> Dict:
    """
    API endpoint to calculate commission for an order.

    Args:
        seller: Seller Profile name
        order_total: Order total amount
        category: Product category

    Returns:
        dict: Commission calculation
    """
    return calculate_commission(
        seller=seller,
        order_total=flt(order_total),
        category=category
    )


@frappe.whitelist()
def manual_release_escrow(sub_order: str) -> Dict:
    """
    Manually release escrow for a sub order (admin only).

    Args:
        sub_order: Sub Order name

    Returns:
        dict: Release result
    """
    if not frappe.has_permission("Sub Order", "write"):
        frappe.throw(_("Not permitted"))

    return process_escrow_release(sub_order)
