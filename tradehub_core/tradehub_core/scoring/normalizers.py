# Copyright (c) 2026, Trade Hub and contributors
# For license information, please see license.txt

"""
Normalization Functions for TradeHub Scoring and Grading.

Provides three normalization strategies used by both the Customer Grading
system (TASK-5) and the Buyer Scoring Engine (TASK-6):

    - normalize_higher_is_better: For metrics where higher values are desirable
      (e.g., order frequency, payment_on_time_rate, feedback_rate)
    - normalize_lower_is_better: For metrics where lower values are desirable
      (e.g., return_rate, dispute_rate, cancellation_rate)
    - normalize_logarithmic: For metrics with diminishing returns at high values
      (e.g., total_spent)

All functions return a normalized score in the range [0.0, 100.0].
All division operations use safe_divide() to prevent ZeroDivisionError.

Usage:
    from tradehub_core.tradehub_core.scoring.normalizers import (
        normalize_higher_is_better,
        normalize_lower_is_better,
        normalize_logarithmic,
    )
"""

import math

from tradehub_core.tradehub_core.utils.safe_math import safe_divide


def normalize_higher_is_better(value, target_good, target_poor):
    """
    Normalize a metric where higher values indicate better performance.

    Maps the value linearly from [target_poor, target_good] to [0, 100].
    Values at or above target_good score 100; values at or below target_poor
    score 0.

    Args:
        value: The raw metric value to normalize.
        target_good: The value at which the metric scores 100 (best).
        target_poor: The value at which the metric scores 0 (worst).

    Returns:
        float: Normalized score in [0.0, 100.0].

    Examples:
        >>> normalize_higher_is_better(100, 100, 0)
        100.0
        >>> normalize_higher_is_better(0, 100, 0)
        0.0
        >>> normalize_higher_is_better(50, 100, 0)
        50.0
        >>> normalize_higher_is_better(200, 100, 0)
        100.0
    """
    if value >= target_good:
        return 100.0
    if value <= target_poor:
        return 0.0
    return safe_divide(value - target_poor, target_good - target_poor) * 100.0


def normalize_lower_is_better(value, target_good, target_poor):
    """
    Normalize a metric where lower values indicate better performance.

    Maps the value linearly from [target_good, target_poor] to [100, 0].
    Values at or below target_good score 100; values at or above target_poor
    score 0.

    Args:
        value: The raw metric value to normalize.
        target_good: The value at which the metric scores 100 (best, lowest).
        target_poor: The value at which the metric scores 0 (worst, highest).

    Returns:
        float: Normalized score in [0.0, 100.0].

    Examples:
        >>> normalize_lower_is_better(0, 0, 10)
        100.0
        >>> normalize_lower_is_better(10, 0, 10)
        0.0
        >>> normalize_lower_is_better(5, 0, 10)
        50.0
        >>> normalize_lower_is_better(-1, 0, 10)
        100.0
    """
    if value <= target_good:
        return 100.0
    if value >= target_poor:
        return 0.0
    return safe_divide(target_poor - value, target_poor - target_good) * 100.0


def normalize_logarithmic(value, target_good):
    """
    Normalize a metric using logarithmic scaling (diminishing returns).

    Useful for monetary values like total_spent where the difference
    between $0 and $1000 is more significant than between $9000 and $10000.

    Uses log10(value + 1) / log10(target_good + 1) to produce a score
    in [0, 100], capped at 100.

    Args:
        value: The raw metric value to normalize. Must be >= 0 for
            meaningful results.
        target_good: The value at which the metric scores 100.

    Returns:
        float: Normalized score in [0.0, 100.0].

    Examples:
        >>> normalize_logarithmic(10000, 10000)
        100.0
        >>> normalize_logarithmic(0, 10000)
        0.0
        >>> normalize_logarithmic(100000, 10000)
        100.0
    """
    if value <= 0:
        return 0.0
    return min(
        100.0,
        safe_divide(math.log10(value + 1), math.log10(target_good + 1)) * 100.0
    )
