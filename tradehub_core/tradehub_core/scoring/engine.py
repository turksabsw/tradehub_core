# Copyright (c) 2026, Trade Hub and contributors
# For license information, please see license.txt

"""
Buyer Scoring Engine for TradeHub B2B Marketplace (TASK-6).

Implements the 8-step scoring pipeline for buyer performance evaluation:
    1. Collect   — Gather raw metric values from Buyer Profile
    2. Normalize — Normalize each metric to a [0, 100] scale
    3. Weight    — Apply metric weights (sum = 100%)
    4. Aggregate — Sum weighted normalized scores into a base score
    5. Curve     — Apply optional scoring curve adjustment
    6. Penalties — Subtract penalty deductions (disputes, chargebacks)
    7. Bonuses   — Add bonus points (tenure, loyalty, improvement)
    8. Finalize  — Clamp to [0, 100], round, and return result

Default Buyer KPI Metrics (STD_BUYER_SCORE, 9 metrics, Σ=100%):
    TOTAL_SPEND         (total_spent)           — 20%, logarithmic
    ORDER_FREQ          (total_orders)          — 15%, logarithmic
    PAYMENT_ON_TIME     (payment_on_time_rate)  — 15%, higher_is_better
    BUYER_RETURN_RATE   (return_rate)           — 10%, lower_is_better
    AVG_ORDER_VAL       (average_order_value)   — 10%, logarithmic
    BUYER_DISPUTE_RATE  (dispute_rate)          — 10%, lower_is_better
    FEEDBACK_RATE       (feedback_rate)         — 5%,  higher_is_better
    ACCOUNT_AGE         (account_age_days)      — 10%, higher_is_better
    LAST_ACTIVITY       (days_since_last_active)— 5%,  lower_is_better

Usage:
    from tradehub_core.tradehub_core.scoring.engine import calculate_buyer_score

    result = calculate_buyer_score(raw_metrics)
    # result = {"score": 72.5, "breakdown": [...], "penalties": 0.0, ...}
"""

import frappe
from frappe.utils import flt, nowdate

from tradehub_core.tradehub_core.utils.safe_math import safe_divide
from tradehub_core.tradehub_core.scoring.normalizers import (
    normalize_higher_is_better,
    normalize_lower_is_better,
    normalize_logarithmic,
)


# Default buyer KPI metric definitions
# Each entry: (kpi_code, metric_field, weight, normalization_type, config)
# config is a dict with normalization-specific parameters
DEFAULT_BUYER_METRICS = [
    ("TOTAL_SPEND", "total_spent", 20, "logarithmic", {"target_good": 10000}),
    ("ORDER_FREQ", "total_orders", 15, "logarithmic", {"target_good": 50}),
    ("PAYMENT_ON_TIME", "payment_on_time_rate", 15, "higher_is_better", {"target_good": 100, "target_poor": 50}),
    ("BUYER_RETURN_RATE", "return_rate", 10, "lower_is_better", {"target_good": 0, "target_poor": 20}),
    ("AVG_ORDER_VAL", "average_order_value", 10, "logarithmic", {"target_good": 5000}),
    ("BUYER_DISPUTE_RATE", "dispute_rate", 10, "lower_is_better", {"target_good": 0, "target_poor": 10}),
    ("FEEDBACK_RATE", "feedback_rate", 5, "higher_is_better", {"target_good": 100, "target_poor": 0}),
    ("ACCOUNT_AGE", "account_age_days", 10, "higher_is_better", {"target_good": 730, "target_poor": 0}),
    ("LAST_ACTIVITY", "days_since_last_active", 5, "lower_is_better", {"target_good": 0, "target_poor": 90}),
]


def calculate_buyer_score(raw_metrics, template_items=None, penalties=None, bonuses=None, curve=None):
    """
    Execute the 8-step buyer scoring pipeline.

    This is the main entry point for buyer score calculation. It accepts
    raw metric values, normalizes them, applies weights, and produces a
    final score with a detailed breakdown.

    Args:
        raw_metrics: Dict of raw buyer metric values. Keys are metric
            field names (e.g., "total_spent", "payment_on_time_rate").
            Values are numeric (int/float). Missing keys default to 0.
        template_items: Optional list of template item dicts, each with:
            - kpi_code (str): KPI identifier (e.g., "TOTAL_SPEND")
            - metric_field (str): Field name in raw_metrics
            - weight (float): Weight as percentage (sum must = 100)
            - normalization_type (str): "higher_is_better", "lower_is_better",
              or "logarithmic"
            - config (dict): Normalization-specific parameters
            If None, uses DEFAULT_BUYER_METRICS.
        penalties: Optional dict with penalty configuration:
            - dispute_count (int): Number of disputes (default 0)
            - penalty_per_dispute (float): Points per dispute (default 3.0)
            - additional_penalty (float): Extra penalty points (default 0)
        bonuses: Optional dict with bonus configuration:
            - tenure_bonus (float): Points for buyer tenure (default 0)
            - loyalty_bonus (float): Points for repeat purchasing (default 0)
            - improvement_bonus (float): Points for score improvement (default 0)
        curve: Optional dict with curve adjustment:
            - type (str): Curve type ("none", "linear_boost", "sqrt")
            - factor (float): Curve adjustment factor (default 1.0)

    Returns:
        dict: {
            "score": float (0-100, final clamped and rounded score),
            "base_score": float (weighted aggregate before adjustments),
            "curved_score": float (score after curve adjustment),
            "total_penalty": float (total penalty deduction),
            "total_bonus": float (total bonus addition),
            "breakdown": list of per-metric dicts with details,
            "penalties_detail": dict of penalty breakdown,
            "bonuses_detail": dict of bonus breakdown,
        }
    """
    # Step 1: Collect — gather raw metrics (already provided as input)
    metrics = _step_collect(raw_metrics)

    # Step 2: Normalize — normalize each metric to [0, 100]
    items = template_items or _build_default_items()
    normalized = _step_normalize(metrics, items)

    # Step 3: Weight — apply weights to normalized scores
    weighted = _step_weight(normalized)

    # Step 4: Aggregate — sum weighted scores into base score
    base_score = _step_aggregate(weighted)

    # Step 5: Curve — apply optional scoring curve
    curved_score = _step_curve(base_score, curve)

    # Step 6: Penalties — subtract penalty deductions
    penalty_result = _step_penalties(curved_score, penalties)
    after_penalties = penalty_result["score_after"]

    # Step 7: Bonuses — add bonus points
    bonus_result = _step_bonuses(after_penalties, bonuses)
    after_bonuses = bonus_result["score_after"]

    # Step 8: Finalize — clamp to [0, 100] and round
    final_score = _step_finalize(after_bonuses)

    # Build breakdown from weighted items
    breakdown = []
    for item in weighted:
        breakdown.append({
            "kpi_code": item["kpi_code"],
            "metric_field": item["metric_field"],
            "raw_value": item["raw_value"],
            "normalized_score": round(item["normalized_score"], 2),
            "weight": item["weight"],
            "weighted_score": round(item["weighted_score"], 2),
        })

    return {
        "score": final_score,
        "base_score": round(base_score, 2),
        "curved_score": round(curved_score, 2),
        "total_penalty": round(penalty_result["total_penalty"], 2),
        "total_bonus": round(bonus_result["total_bonus"], 2),
        "breakdown": breakdown,
        "penalties_detail": penalty_result["detail"],
        "bonuses_detail": bonus_result["detail"],
    }


def calculate_buyer_score_for_profile(buyer_name):
    """
    Calculate the buyer score for a specific Buyer Profile and persist results.

    Reads raw metrics from the Buyer Profile, runs the 8-step scoring
    pipeline, and writes buyer_score and buyer_score_trend back to the
    Buyer Profile using frappe.db.set_value (to avoid triggering all hooks).

    Args:
        buyer_name: Name of the Buyer Profile document.

    Returns:
        dict: Full scoring result from calculate_buyer_score().

    Raises:
        frappe.DoesNotExistError: If the Buyer Profile does not exist.
    """
    from datetime import datetime

    buyer = frappe.get_doc("Buyer Profile", buyer_name)

    # Collect raw metrics from buyer profile fields
    raw_metrics = _collect_buyer_metrics(buyer)

    # Get previous score for trend calculation
    previous_score = flt(buyer.buyer_score)

    # Run the scoring pipeline
    result = calculate_buyer_score(raw_metrics)

    # Determine score trend
    new_score = result["score"]
    trend = _determine_trend(new_score, previous_score)

    # Persist results using set_value to avoid triggering full validation
    frappe.db.set_value("Buyer Profile", buyer_name, {
        "buyer_score": new_score,
        "buyer_score_trend": trend,
        "last_score_date": nowdate(),
    }, update_modified=False)

    result["trend"] = trend
    result["previous_score"] = previous_score

    return result


def _collect_buyer_metrics(buyer):
    """
    Extract raw metric values from a Buyer Profile document.

    Computes derived metrics (account_age_days, days_since_last_active)
    from timestamp fields on the Buyer Profile.

    Args:
        buyer: Buyer Profile Document instance.

    Returns:
        dict: Raw metric field → value mappings.
    """
    from datetime import datetime

    metrics = {
        "total_spent": flt(buyer.total_spent),
        "total_orders": flt(buyer.total_orders),
        "payment_on_time_rate": flt(buyer.payment_on_time_rate),
        "return_rate": flt(buyer.return_rate),
        "average_order_value": flt(buyer.average_order_value),
        "dispute_rate": flt(buyer.dispute_rate),
        "feedback_rate": flt(buyer.feedback_rate),
    }

    # Calculate account age in days from joined_at
    if buyer.joined_at:
        try:
            joined = buyer.joined_at
            if isinstance(joined, str):
                joined = datetime.fromisoformat(joined.replace("Z", "+00:00"))
            now = datetime.now()
            if hasattr(joined, "tzinfo") and joined.tzinfo:
                joined = joined.replace(tzinfo=None)
            metrics["account_age_days"] = max(0, (now - joined).days)
        except (ValueError, TypeError):
            metrics["account_age_days"] = 0
    else:
        metrics["account_age_days"] = 0

    # Calculate days since last active from last_active_at
    if buyer.last_active_at:
        try:
            last_active = buyer.last_active_at
            if isinstance(last_active, str):
                last_active = datetime.fromisoformat(last_active.replace("Z", "+00:00"))
            now = datetime.now()
            if hasattr(last_active, "tzinfo") and last_active.tzinfo:
                last_active = last_active.replace(tzinfo=None)
            metrics["days_since_last_active"] = max(0, (now - last_active).days)
        except (ValueError, TypeError):
            metrics["days_since_last_active"] = 90  # Assume inactive on error
    else:
        metrics["days_since_last_active"] = 90  # Assume inactive if never active

    return metrics


def _determine_trend(new_score, previous_score):
    """
    Determine the buyer score trend based on score change.

    Args:
        new_score: The newly calculated score.
        previous_score: The previous buyer score.

    Returns:
        str: One of "Up", "Down", "Stable", or "New".
    """
    if flt(previous_score) == 0 and flt(new_score) > 0:
        return "New"

    change = flt(new_score) - flt(previous_score)

    if change > 1:
        return "Up"
    elif change < -1:
        return "Down"
    else:
        return "Stable"


# --- Pipeline Step Functions ---


def _step_collect(raw_metrics):
    """
    Step 1: Collect raw metric values.

    Ensures all metric values are numeric (defaults to 0 for missing/None).

    Args:
        raw_metrics: Dict of raw metric field → value mappings.

    Returns:
        dict: Cleaned metrics dict with numeric values.
    """
    cleaned = {}
    for key, value in (raw_metrics or {}).items():
        cleaned[key] = flt(value)
    return cleaned


def _step_normalize(metrics, items):
    """
    Step 2: Normalize each metric to a [0, 100] scale.

    Routes each metric to the appropriate normalization function based on
    its normalization_type.

    Args:
        metrics: Dict of cleaned metric values from Step 1.
        items: List of template item dicts with normalization config.

    Returns:
        list: Items enriched with raw_value and normalized_score.
    """
    result = []
    for item in items:
        metric_field = item["metric_field"]
        raw_value = metrics.get(metric_field, 0)
        normalization_type = item.get("normalization_type", "higher_is_better")
        config = item.get("config", {})

        normalized = _normalize_value(raw_value, normalization_type, config)

        result.append({
            "kpi_code": item["kpi_code"],
            "metric_field": metric_field,
            "weight": item["weight"],
            "raw_value": raw_value,
            "normalized_score": normalized,
        })

    return result


def _normalize_value(value, normalization_type, config):
    """
    Normalize a single value using the specified strategy.

    Uses the tradehub_core normalizers:
        - higher_is_better: For metrics where higher values are desirable
        - lower_is_better: For metrics where lower values are desirable
        - logarithmic: For metrics with diminishing returns at high values

    Args:
        value: Raw metric value.
        normalization_type: One of "higher_is_better", "lower_is_better",
            or "logarithmic".
        config: Dict of normalization parameters specific to the type.

    Returns:
        float: Normalized score in [0.0, 100.0].
    """
    if normalization_type == "higher_is_better":
        return normalize_higher_is_better(
            value,
            target_good=config.get("target_good", 100),
            target_poor=config.get("target_poor", 0),
        )
    elif normalization_type == "lower_is_better":
        return normalize_lower_is_better(
            value,
            target_good=config.get("target_good", 0),
            target_poor=config.get("target_poor", 10),
        )
    elif normalization_type == "logarithmic":
        return normalize_logarithmic(
            value,
            target_good=config.get("target_good", 10000),
        )
    else:
        # Default to higher_is_better for unknown types
        return normalize_higher_is_better(
            value,
            target_good=config.get("target_good", 100),
            target_poor=config.get("target_poor", 0),
        )


def _step_weight(normalized_items):
    """
    Step 3: Apply weights to normalized scores.

    Multiplies each normalized score by its weight percentage.

    Args:
        normalized_items: List of items with normalized_score and weight.

    Returns:
        list: Items enriched with weighted_score.
    """
    result = []
    for item in normalized_items:
        weighted_score = item["normalized_score"] * safe_divide(item["weight"], 100, default=0)
        result.append({
            **item,
            "weighted_score": weighted_score,
        })
    return result


def _step_aggregate(weighted_items):
    """
    Step 4: Aggregate weighted scores into a base score.

    Sums all weighted scores. If weights sum to 100%, the base score
    will be in [0, 100]. If weights don't sum to 100%, the score is
    proportionally adjusted.

    Args:
        weighted_items: List of items with weighted_score.

    Returns:
        float: Aggregated base score.
    """
    total_weighted = sum(item["weighted_score"] for item in weighted_items)
    total_weight = sum(item["weight"] for item in weighted_items)

    # If weights don't sum to 100, normalize proportionally
    if total_weight > 0 and abs(total_weight - 100) > 0.01:
        return safe_divide(total_weighted * 100, total_weight, default=0)

    return total_weighted


def _step_curve(base_score, curve=None):
    """
    Step 5: Apply optional scoring curve adjustment.

    Supports curve types:
        - "none": No adjustment (default)
        - "linear_boost": Multiply by factor (e.g., 1.05 for 5% boost)
        - "sqrt": Square root scaling (compresses high scores,
          expands low scores)

    Args:
        base_score: The aggregated base score from Step 4.
        curve: Optional dict with "type" and "factor" keys.

    Returns:
        float: Curve-adjusted score.
    """
    if not curve:
        return base_score

    curve_type = curve.get("type", "none")
    factor = flt(curve.get("factor", 1.0))

    if curve_type == "none":
        return base_score
    elif curve_type == "linear_boost":
        return base_score * factor
    elif curve_type == "sqrt":
        # Scale: sqrt(score/100) * 100 * factor
        if base_score <= 0:
            return 0.0
        import math
        return math.sqrt(safe_divide(base_score, 100, default=0)) * 100.0 * factor
    else:
        return base_score


def _step_penalties(score, penalties=None):
    """
    Step 6: Apply penalty deductions.

    Subtracts penalty points based on dispute count and any
    additional penalty amount.

    Args:
        score: Current score after curve adjustment.
        penalties: Optional dict with:
            - dispute_count (int): Number of disputes
            - penalty_per_dispute (float): Points deducted per dispute
            - additional_penalty (float): Extra penalty points

    Returns:
        dict: {
            "score_after": float (score after penalties),
            "total_penalty": float (total points deducted),
            "detail": dict (breakdown of penalty components)
        }
    """
    if not penalties:
        return {
            "score_after": score,
            "total_penalty": 0.0,
            "detail": {
                "dispute_count": 0,
                "dispute_penalty": 0.0,
                "additional_penalty": 0.0,
            },
        }

    disputes = max(0, int(penalties.get("dispute_count", 0)))
    per_dispute = flt(penalties.get("penalty_per_dispute", 3.0))
    additional = flt(penalties.get("additional_penalty", 0))

    dispute_penalty = disputes * per_dispute
    total_penalty = dispute_penalty + additional

    return {
        "score_after": score - total_penalty,
        "total_penalty": total_penalty,
        "detail": {
            "dispute_count": disputes,
            "dispute_penalty": round(dispute_penalty, 2),
            "additional_penalty": round(additional, 2),
        },
    }


def _step_bonuses(score, bonuses=None):
    """
    Step 7: Apply bonus additions.

    Adds bonus points for tenure, loyalty, and improvement.

    Args:
        score: Current score after penalties.
        bonuses: Optional dict with:
            - tenure_bonus (float): Points for buyer tenure
            - loyalty_bonus (float): Points for repeat purchasing loyalty
            - improvement_bonus (float): Points for score improvement trend

    Returns:
        dict: {
            "score_after": float (score after bonuses),
            "total_bonus": float (total points added),
            "detail": dict (breakdown of bonus components)
        }
    """
    if not bonuses:
        return {
            "score_after": score,
            "total_bonus": 0.0,
            "detail": {
                "tenure_bonus": 0.0,
                "loyalty_bonus": 0.0,
                "improvement_bonus": 0.0,
            },
        }

    tenure = flt(bonuses.get("tenure_bonus", 0))
    loyalty = flt(bonuses.get("loyalty_bonus", 0))
    improvement = flt(bonuses.get("improvement_bonus", 0))

    total_bonus = tenure + loyalty + improvement

    return {
        "score_after": score + total_bonus,
        "total_bonus": total_bonus,
        "detail": {
            "tenure_bonus": round(tenure, 2),
            "loyalty_bonus": round(loyalty, 2),
            "improvement_bonus": round(improvement, 2),
        },
    }


def _step_finalize(score):
    """
    Step 8: Finalize the score.

    Clamps the score to [0, 100] and rounds to 2 decimal places.

    Args:
        score: The score after all adjustments.

    Returns:
        float: Final score in [0.0, 100.0], rounded to 2 decimal places.
    """
    return round(max(0.0, min(100.0, score)), 2)


def _build_default_items():
    """
    Build the default buyer KPI template items from DEFAULT_BUYER_METRICS.

    Returns:
        list: List of template item dicts with kpi_code, metric_field,
            weight, normalization_type, and config.
    """
    items = []
    for kpi_code, metric_field, weight, norm_type, config in DEFAULT_BUYER_METRICS:
        items.append({
            "kpi_code": kpi_code,
            "metric_field": metric_field,
            "weight": weight,
            "normalization_type": norm_type,
            "config": config,
        })
    return items
