# Copyright (c) 2026, Trade Hub and contributors
# For license information, please see license.txt

"""
Safe Math Utilities for Trade Hub Scoring and Metric Calculations.

This module provides safe arithmetic helper functions used across all
scoring, grading, and metric calculation pipelines. Every rate/ratio
computation in the system must use these helpers to prevent division
by zero errors and ensure consistent default behavior.

Usage:
    from tradehub_core.tradehub_core.utils.safe_math import safe_divide

    rate = safe_divide(defect_count, total_orders)  # returns 0 if total_orders is 0
    ratio = safe_divide(score, max_score, default=None)  # returns None if no data
"""

from typing import Optional, Union

# Numeric type alias for type hints
Numeric = Union[int, float]


def safe_divide(
    numerator: Numeric,
    denominator: Numeric,
    default: Optional[Numeric] = 0
) -> Optional[Numeric]:
    """
    Safely divide two numbers, returning a default value when division is not possible.

    This function prevents ZeroDivisionError and handles falsy denominators
    (0, 0.0, None). It must be used in ALL metric and rate calculations
    across the scoring, grading, and KPI systems.

    Args:
        numerator: The dividend value.
        denominator: The divisor value. If falsy (0, 0.0, None), returns default.
        default: Value to return when division is not possible. Use 0 for rates
            where "no data" means zero. Use None when "no data" is semantically
            different from zero.

    Returns:
        The result of numerator / denominator, or the default value if
        denominator is falsy.

    Examples:
        >>> safe_divide(10, 2)
        5.0
        >>> safe_divide(10, 0)
        0
        >>> safe_divide(0, 0)
        0
        >>> safe_divide(100, 0, default=None)
        None
        >>> safe_divide(7, 3)
        2.3333333333333335
    """
    if not denominator:
        return default
    return numerator / denominator
