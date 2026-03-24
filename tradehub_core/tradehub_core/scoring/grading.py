# Copyright (c) 2026, Trade Hub and contributors
# For license information, please see license.txt

"""
Customer Grading System for TradeHub B2B Marketplace (TASK-5).

Implements the A-F grading pipeline for buyer customers:
    1. Collect raw buyer metrics from Buyer Profile
    2. Normalize each metric using appropriate strategy
    3. Apply criterion weights (sum = 100%)
    4. Aggregate weighted scores into a final score
    5. Map final score to a grade letter (A-F)

Grade Boundaries:
    A >= 85  (Excellent buyer)
    B >= 70  (Good buyer)
    C >= 55  (Average buyer / provisional for new buyers)
    D >= 40  (Below average)
    E >= 25  (Poor)
    F <  25  (Very poor)

Grading Weights (default):
    Total Spend:        25%  (Logarithmic, target: 10,000 TRY)
    Order Frequency:    20%  (Higher is Better, target: 4.0/month)
    On-Time Payment:    15%  (Higher is Better, 50-100%)
    Return/Cancel Rate: 15%  (Lower is Better, 0-20%)
    Account Age:        10%  (Higher is Better, 0-730 days)
    Dispute Rate:       10%  (Lower is Better, 0-10%)
    Feedback Rate:       5%  (Higher is Better, 0-100%)

Usage:
    from tradehub_core.tradehub_core.scoring.grading import (
        score_to_grade,
        calculate_customer_grade_score,
    )

    grade = score_to_grade(85.0)  # "A"
    result = calculate_customer_grade_score(metrics, criteria)
"""

from tradehub_core.tradehub_core.scoring.normalizers import (
    normalize_higher_is_better,
    normalize_lower_is_better,
    normalize_logarithmic,
)


# Grade boundary thresholds (inclusive lower bounds)
GRADE_BOUNDARIES = [
    (85, "A"),
    (70, "B"),
    (55, "C"),
    (40, "D"),
    (25, "E"),
]

# Default grading criteria configuration
# Each criterion: (code, weight, normalization_type, target_good, target_poor)
DEFAULT_GRADING_CRITERIA = [
    ("TOTAL_SPEND", 25, "logarithmic", 10000, None),
    ("ORDER_FREQUENCY", 20, "higher_is_better", 4.0, 0),
    ("ON_TIME_PAYMENT", 15, "higher_is_better", 100, 50),
    ("RETURN_CANCEL_RATE", 15, "lower_is_better", 0, 20),
    ("ACCOUNT_AGE", 10, "higher_is_better", 730, 0),
    ("DISPUTE_RATE", 10, "lower_is_better", 0, 10),
    ("FEEDBACK_RATE", 5, "higher_is_better", 100, 0),
]


def score_to_grade(score):
    """
    Map a numeric score (0-100) to a grade letter (A-F).

    The score is rounded to 2 decimal places before comparison to handle
    floating-point precision issues. Grade boundaries use >= (inclusive
    lower bound).

    Args:
        score: Numeric score in the range [0, 100].

    Returns:
        str: Grade letter ("A", "B", "C", "D", "E", or "F").

    Examples:
        >>> score_to_grade(85.0)
        'A'
        >>> score_to_grade(84.99)
        'B'
        >>> score_to_grade(70)
        'B'
        >>> score_to_grade(55)
        'C'
        >>> score_to_grade(40)
        'D'
        >>> score_to_grade(25)
        'E'
        >>> score_to_grade(24.99)
        'F'
        >>> score_to_grade(0)
        'F'
    """
    score = round(score, 2)
    for boundary, grade in GRADE_BOUNDARIES:
        if score >= boundary:
            return grade
    return "F"


def normalize_metric(value, normalization_type, target_good, target_poor=None):
    """
    Normalize a single metric value based on its normalization strategy.

    Routes to the appropriate normalization function based on the
    normalization_type parameter.

    Args:
        value: The raw metric value.
        normalization_type: One of "higher_is_better", "lower_is_better",
            or "logarithmic".
        target_good: The value representing excellent performance.
        target_poor: The value representing poor performance (not used
            for logarithmic normalization).

    Returns:
        float: Normalized score in [0.0, 100.0].

    Raises:
        ValueError: If normalization_type is not recognized.
    """
    if normalization_type == "higher_is_better":
        return normalize_higher_is_better(value, target_good, target_poor or 0)
    elif normalization_type == "lower_is_better":
        return normalize_lower_is_better(value, target_good, target_poor or 0)
    elif normalization_type == "logarithmic":
        return normalize_logarithmic(value, target_good)
    else:
        raise ValueError(f"Unknown normalization type: {normalization_type}")


def calculate_customer_grade_score(metrics, criteria=None):
    """
    Calculate the overall customer grade score from raw buyer metrics.

    Implements the grading pipeline:
        1. For each criterion, extract the corresponding metric value
        2. Normalize the value using the criterion's normalization strategy
        3. Multiply by the criterion weight (as percentage)
        4. Sum all weighted normalized scores
        5. Map to grade letter

    Args:
        metrics: Dict of raw buyer metric values. Expected keys depend on
            the criteria used. For default criteria:
            - total_spend (float): Total purchase amount in TRY
            - order_frequency (float): Average orders per month
            - on_time_payment (float): Payment on-time rate (0-100%)
            - return_cancel_rate (float): Combined return + cancel rate (0-100%)
            - account_age (int): Account age in days
            - dispute_rate (float): Dispute rate (0-100%)
            - feedback_rate (float): Feedback submission rate (0-100%)
        criteria: Optional list of criterion dicts, each with keys:
            - criterion_code (str): Metric identifier
            - weight (float): Weight as percentage (0-100, sum must = 100)
            - normalization_type (str): "higher_is_better", "lower_is_better",
              or "logarithmic"
            - target_good (float): Value for score of 100
            - target_poor (float): Value for score of 0 (not used for logarithmic)
            If None, uses DEFAULT_GRADING_CRITERIA.

    Returns:
        dict: {
            "score": float (0-100, rounded to 2 decimal places),
            "grade": str (A-F),
            "breakdown": list of dicts with per-criterion details
        }
    """
    if criteria is None:
        criteria = _build_default_criteria()

    # Mapping from criterion_code to metrics dict key
    metric_key_map = {
        "TOTAL_SPEND": "total_spend",
        "ORDER_FREQUENCY": "order_frequency",
        "ON_TIME_PAYMENT": "on_time_payment",
        "RETURN_CANCEL_RATE": "return_cancel_rate",
        "ACCOUNT_AGE": "account_age",
        "DISPUTE_RATE": "dispute_rate",
        "FEEDBACK_RATE": "feedback_rate",
    }

    total_score = 0.0
    breakdown = []

    for criterion in criteria:
        code = criterion.get("criterion_code", "")
        weight = criterion.get("weight", 0)
        normalization_type = criterion.get("normalization_type", "higher_is_better")
        target_good = criterion.get("target_good", 100)
        target_poor = criterion.get("target_poor", 0)

        # Get raw metric value (default 0 for missing metrics)
        metric_key = metric_key_map.get(code, code.lower())
        raw_value = metrics.get(metric_key, 0) or 0

        # Normalize the raw value
        normalized = normalize_metric(
            raw_value, normalization_type, target_good, target_poor
        )

        # Apply weight (weight is a percentage, so divide by 100)
        weighted = normalized * (weight / 100.0)
        total_score += weighted

        breakdown.append({
            "criterion_code": code,
            "raw_value": raw_value,
            "normalized_score": round(normalized, 2),
            "weight": weight,
            "weighted_score": round(weighted, 2),
        })

    final_score = round(total_score, 2)
    grade = score_to_grade(final_score)

    return {
        "score": final_score,
        "grade": grade,
        "breakdown": breakdown,
    }


def _build_default_criteria():
    """
    Build the default grading criteria list from DEFAULT_GRADING_CRITERIA.

    Returns:
        list: List of criterion dicts with keys: criterion_code, weight,
            normalization_type, target_good, target_poor.
    """
    criteria = []
    for code, weight, norm_type, target_good, target_poor in DEFAULT_GRADING_CRITERIA:
        criteria.append({
            "criterion_code": code,
            "weight": weight,
            "normalization_type": norm_type,
            "target_good": target_good,
            "target_poor": target_poor,
        })
    return criteria
