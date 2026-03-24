# Copyright (c) 2024, TR TradeHub and contributors
# For license information, please see license.txt

"""
KPI Dashboard API Endpoints for TradeHub.

Provides three whitelisted API endpoints for KPI dashboard data:

1. get_buyer_kpi_dashboard_data(buyer_name) — Full detail for a buyer
2. get_buyer_kpi_summary_for_seller(seller_name, anonymous_customer_id)
   — Anonymous summary visible to sellers
3. get_admin_kpi_dashboard_data(tenant, period) — Platform aggregate
   with grade distribution for admins
"""

import frappe
from frappe import _
from frappe.utils import cint, flt

from tradehub_core.tradehub_core.utils.safe_math import safe_divide


@frappe.whitelist()
def get_buyer_kpi_dashboard_data(buyer_name):
	"""Get full KPI dashboard data for a buyer.

	Returns comprehensive KPI data including overall score, grade,
	metric breakdown, trend history, and raw metrics. Intended for
	the buyer themselves or system administrators.

	Args:
		buyer_name: Buyer Profile name (e.g., "BUYER-00001").

	Returns:
		dict: Dashboard data with keys:
			- buyer_info: Basic buyer information
			- current_score: Latest finalized KPI score details
			- score_breakdown: Per-metric breakdown of latest score
			- score_history: List of recent finalized scores (up to 12)
			- grade_info: Latest customer grade details
			- summary_stats: Aggregated statistics

	Raises:
		frappe.PermissionError: If user lacks permission to view buyer data.
		frappe.DoesNotExistError: If buyer_name does not exist.

	Example:
		frappe.call({
			method: "tradehub_core.tradehub_core.api.kpi_dashboard.get_buyer_kpi_dashboard_data",
			args: { buyer_name: "BUYER-00001" }
		})
	"""
	if not buyer_name:
		frappe.throw(_("Buyer name is required"))

	if not frappe.db.exists("Buyer Profile", buyer_name):
		frappe.throw(_("Buyer not found"), frappe.DoesNotExistError)

	# Permission check: only the buyer's own user, System Manager, or
	# users with Marketplace Manager role can access full dashboard
	_check_buyer_permission(buyer_name)

	# 1. Buyer info
	buyer_info = _get_buyer_info(buyer_name)

	# 2. Current (latest finalized) KPI score
	current_score = _get_latest_kpi_score(buyer_name)

	# 3. Score breakdown from latest score
	score_breakdown = []
	if current_score:
		score_breakdown = _get_score_breakdown(current_score.get("name"))

	# 4. Score history (up to 12 recent scores)
	score_history = _get_score_history(buyer_name, limit=12)

	# 5. Grade info
	grade_info = _get_latest_grade(buyer_name)

	# 6. Summary statistics
	summary_stats = _get_buyer_summary_stats(buyer_name)

	return {
		"buyer_info": buyer_info,
		"current_score": current_score,
		"score_breakdown": score_breakdown,
		"score_history": score_history,
		"grade_info": grade_info,
		"summary_stats": summary_stats,
	}


@frappe.whitelist()
def get_buyer_kpi_summary_for_seller(seller_name, anonymous_customer_id):
	"""Get anonymous KPI summary of a buyer for a seller.

	Returns a privacy-preserving summary of a buyer's KPI performance.
	The buyer's identity is hidden behind the anonymous_customer_id
	(SHA-256 hash). Sellers can only see aggregate scores, grade,
	and high-level metrics — not the full buyer profile.

	Args:
		seller_name: Seller Profile name (e.g., "SELLER-00001").
		anonymous_customer_id: SHA-256 anonymous customer ID from
			Seller Customer Grade record.

	Returns:
		dict: Anonymous summary with keys:
			- anonymous_customer_id: The hashed identifier
			- seller_grade: Grade assigned by seller (or platform grade)
			- platform_grade: Platform-assigned grade (A-F)
			- platform_score: Platform-assigned score (0-100)
			- order_stats: Anonymized order statistics
			- kpi_summary: High-level KPI score summary (if available)

	Raises:
		frappe.PermissionError: If user lacks permission.
		frappe.DoesNotExistError: If seller or grade record not found.

	Example:
		frappe.call({
			method: "tradehub_core.tradehub_core.api.kpi_dashboard.get_buyer_kpi_summary_for_seller",
			args: {
				seller_name: "SELLER-00001",
				anonymous_customer_id: "a1b2c3d4..."
			}
		})
	"""
	if not seller_name:
		frappe.throw(_("Seller name is required"))

	if not anonymous_customer_id:
		frappe.throw(_("Anonymous customer ID is required"))

	if not frappe.db.exists("Seller Profile", seller_name):
		frappe.throw(_("Seller not found"), frappe.DoesNotExistError)

	# Permission check: only the seller's own user or system admins
	_check_seller_permission(seller_name)

	# Find the Seller Customer Grade record by anonymous_customer_id + seller
	grade_record = frappe.db.get_value(
		"Seller Customer Grade",
		{
			"seller": seller_name,
			"anonymous_customer_id": anonymous_customer_id,
			"status": "Active",
		},
		[
			"name", "anonymous_customer_id", "buyer",
			"platform_grade", "platform_score",
			"seller_grade", "seller_score",
			"use_custom_criteria",
			"total_orders", "total_spend", "average_order_value",
			"order_frequency", "on_time_payment_rate",
			"return_rate", "dispute_rate", "cancellation_rate",
			"first_order_date", "last_order_date",
			"calculation_date",
		],
		as_dict=True,
	)

	if not grade_record:
		frappe.throw(
			_("No grade record found for this customer"),
			frappe.DoesNotExistError,
		)

	# Build anonymous summary — do NOT expose the buyer field
	summary = {
		"anonymous_customer_id": grade_record.anonymous_customer_id,
		"platform_grade": grade_record.platform_grade,
		"platform_score": flt(grade_record.platform_score),
		"seller_grade": grade_record.seller_grade,
		"seller_score": flt(grade_record.seller_score),
		"use_custom_criteria": cint(grade_record.use_custom_criteria),
		"calculation_date": grade_record.calculation_date,
		"order_stats": {
			"total_orders": cint(grade_record.total_orders),
			"total_spend": flt(grade_record.total_spend),
			"average_order_value": flt(grade_record.average_order_value),
			"order_frequency": flt(grade_record.order_frequency),
			"first_order_date": grade_record.first_order_date,
			"last_order_date": grade_record.last_order_date,
		},
		"performance": {
			"on_time_payment_rate": flt(grade_record.on_time_payment_rate),
			"return_rate": flt(grade_record.return_rate),
			"dispute_rate": flt(grade_record.dispute_rate),
			"cancellation_rate": flt(grade_record.cancellation_rate),
		},
	}

	# Add KPI score summary if available (from Buyer KPI Score Log)
	buyer = grade_record.buyer
	if buyer:
		kpi_summary = _get_anonymous_kpi_summary(buyer)
		if kpi_summary:
			summary["kpi_summary"] = kpi_summary

	return summary


@frappe.whitelist()
def get_admin_kpi_dashboard_data(tenant=None, period=None):
	"""Get platform-wide KPI dashboard data for administrators.

	Returns aggregate KPI statistics across all buyers, including
	grade distribution, score distribution, trend analysis, and
	per-period summaries. Only accessible by System Manager or
	Marketplace Manager roles.

	Args:
		tenant: Optional tenant filter for multi-tenant isolation.
		period: Optional period filter (e.g., "2024-01", "2024-Q1").

	Returns:
		dict: Admin dashboard data with keys:
			- platform_summary: Overall platform KPI stats
			- grade_distribution: Count per grade (A-F)
			- score_distribution: Count per score range
			- trend_data: Recent period-over-period trends
			- top_performers: Top 10 buyers by score
			- at_risk_buyers: Buyers with declining scores

	Raises:
		frappe.PermissionError: If user is not System Manager or
			Marketplace Manager.

	Example:
		frappe.call({
			method: "tradehub_core.tradehub_core.api.kpi_dashboard.get_admin_kpi_dashboard_data",
			args: { tenant: "TENANT-00001", period: "2024-01" }
		})
	"""
	# Permission check: admin-only
	_check_admin_permission()

	# 1. Platform summary
	platform_summary = _get_platform_summary(tenant, period)

	# 2. Grade distribution
	grade_distribution = _get_grade_distribution(tenant, period)

	# 3. Score distribution
	score_distribution = _get_score_distribution(tenant, period)

	# 4. Trend data (recent periods)
	trend_data = _get_trend_data(tenant)

	# 5. Top performers
	top_performers = _get_top_performers(tenant, limit=10)

	# 6. At-risk buyers (declining scores)
	at_risk_buyers = _get_at_risk_buyers(tenant, limit=10)

	return {
		"platform_summary": platform_summary,
		"grade_distribution": grade_distribution,
		"score_distribution": score_distribution,
		"trend_data": trend_data,
		"top_performers": top_performers,
		"at_risk_buyers": at_risk_buyers,
	}


# ---------------------------------------------------------------------------
# Permission check helpers
# ---------------------------------------------------------------------------

def _check_buyer_permission(buyer_name):
	"""Check if current user can view buyer KPI dashboard data.

	Allows access for:
	- The buyer's own linked user
	- System Manager role
	- Marketplace Manager role (if the role exists)

	Args:
		buyer_name: Buyer Profile name.

	Raises:
		frappe.PermissionError: If user lacks permission.
	"""
	user = frappe.session.user
	if user == "Administrator":
		return

	roles = frappe.get_roles(user)
	if "System Manager" in roles:
		return
	if "Marketplace Manager" in roles:
		return

	# Check if the user is the buyer's own user
	buyer_user = frappe.db.get_value("Buyer Profile", buyer_name, "user")
	if buyer_user and buyer_user == user:
		return

	frappe.throw(
		_("You do not have permission to view this buyer's KPI data"),
		frappe.PermissionError,
	)


def _check_seller_permission(seller_name):
	"""Check if current user can view seller-side buyer summary.

	Allows access for:
	- The seller's own linked user
	- System Manager role
	- Marketplace Manager role

	Args:
		seller_name: Seller Profile name.

	Raises:
		frappe.PermissionError: If user lacks permission.
	"""
	user = frappe.session.user
	if user == "Administrator":
		return

	roles = frappe.get_roles(user)
	if "System Manager" in roles:
		return
	if "Marketplace Manager" in roles:
		return

	# Check if the user is the seller's own user
	seller_user = frappe.db.get_value("Seller Profile", seller_name, "user")
	if seller_user and seller_user == user:
		return

	frappe.throw(
		_("You do not have permission to view this data"),
		frappe.PermissionError,
	)


def _check_admin_permission():
	"""Check if current user has admin permission for platform dashboard.

	Allows access for:
	- Administrator
	- System Manager role
	- Marketplace Manager role

	Raises:
		frappe.PermissionError: If user lacks permission.
	"""
	user = frappe.session.user
	if user == "Administrator":
		return

	roles = frappe.get_roles(user)
	if "System Manager" in roles:
		return
	if "Marketplace Manager" in roles:
		return

	frappe.throw(
		_("You do not have permission to view the admin KPI dashboard"),
		frappe.PermissionError,
	)


# ---------------------------------------------------------------------------
# Data retrieval helpers — Buyer dashboard
# ---------------------------------------------------------------------------

def _get_buyer_info(buyer_name):
	"""Get basic buyer profile information for the dashboard.

	Args:
		buyer_name: Buyer Profile name.

	Returns:
		dict: Buyer info with name, display name, status, level, score, etc.
	"""
	fields = [
		"name", "buyer_name", "display_name", "status",
		"buyer_level", "buyer_level_name",
		"buyer_score", "buyer_score_trend", "last_score_date",
		"total_orders", "total_spent", "average_order_value",
		"payment_on_time_rate", "payment_pattern",
		"return_rate", "feedback_rate", "dispute_rate", "cancellation_rate",
		"joined_at", "last_active_at",
	]

	buyer = frappe.db.get_value("Buyer Profile", buyer_name, fields, as_dict=True)
	return buyer or {}


def _get_latest_kpi_score(buyer_name):
	"""Get the latest finalized KPI score for a buyer.

	Args:
		buyer_name: Buyer Profile name.

	Returns:
		dict or None: Latest KPI score record, or None if no finalized scores.
	"""
	score = frappe.db.get_value(
		"Buyer KPI Score Log",
		{
			"buyer": buyer_name,
			"status": "Finalized",
		},
		[
			"name", "overall_score", "previous_score", "score_change",
			"score_trend", "grade", "passing_status", "percentile_rank",
			"kpi_template", "score_type", "score_period",
			"calculation_date", "evaluation_date",
			"buyer_level_at_calculation",
			"total_weighted_score", "metrics_evaluated",
			"metrics_passing", "metrics_warning", "metrics_critical",
			"highest_metric", "lowest_metric",
			"total_spend", "average_order_value", "order_count",
			"order_frequency", "payment_on_time_rate", "payment_pattern",
			"return_rate", "cancellation_rate", "feedback_rate",
			"dispute_rate", "account_age_days", "last_activity_days",
			"bonus_points", "penalty_deduction",
		],
		as_dict=True,
		order_by="calculation_date desc",
	)

	return score


def _get_score_breakdown(score_name):
	"""Get per-metric breakdown from a KPI score record's child table.

	Args:
		score_name: Buyer KPI Score Log name.

	Returns:
		list: List of metric score dicts with code, name, scores, weight, level.
	"""
	if not score_name:
		return []

	metrics = frappe.get_all(
		"Buyer KPI Metric Score",
		filters={"parent": score_name, "parenttype": "Buyer KPI Score Log"},
		fields=[
			"metric_code", "metric_name",
			"raw_value", "normalized_score",
			"weight", "weighted_score",
			"threshold_level",
		],
		order_by="idx asc",
	)

	return metrics


def _get_score_history(buyer_name, limit=12):
	"""Get recent finalized KPI score history for a buyer.

	Returns data suitable for chart rendering (score over time).

	Args:
		buyer_name: Buyer Profile name.
		limit: Maximum number of records to return.

	Returns:
		list: Score history records sorted by calculation_date ascending.
	"""
	scores = frappe.get_all(
		"Buyer KPI Score Log",
		filters={
			"buyer": buyer_name,
			"status": "Finalized",
		},
		fields=[
			"name", "calculation_date", "score_period",
			"overall_score", "score_change", "score_trend",
			"grade", "passing_status",
		],
		order_by="calculation_date desc",
		limit=cint(limit),
	)

	# Reverse to get chronological order for chart rendering
	scores.reverse()
	return scores


def _get_latest_grade(buyer_name):
	"""Get the latest finalized customer grade for a buyer.

	Args:
		buyer_name: Buyer Profile name.

	Returns:
		dict or None: Latest grade record, or None if no finalized grades.
	"""
	grade = frappe.db.get_value(
		"Customer Grade",
		{
			"buyer": buyer_name,
			"status": "Finalized",
		},
		[
			"name", "grade", "overall_score", "grade_type", "grade_period",
			"calculation_date", "score_change", "score_trend",
			"is_provisional", "orders_evaluated",
		],
		as_dict=True,
		order_by="calculation_date desc",
	)

	return grade


def _get_buyer_summary_stats(buyer_name):
	"""Get aggregated summary statistics for a buyer's KPI history.

	Args:
		buyer_name: Buyer Profile name.

	Returns:
		dict: Summary stats including avg score, total evaluations, etc.
	"""
	# Use parameterized queries to prevent SQL injection
	stats = frappe.db.sql("""
		SELECT
			COUNT(*) as total_evaluations,
			AVG(overall_score) as avg_score,
			MIN(overall_score) as min_score,
			MAX(overall_score) as max_score,
			SUM(CASE WHEN passing_status = 'Passed' THEN 1 ELSE 0 END) as passed_count,
			SUM(CASE WHEN passing_status = 'Failed' THEN 1 ELSE 0 END) as failed_count
		FROM `tabBuyer KPI Score Log`
		WHERE buyer = %(buyer)s
		AND status = 'Finalized'
	""", {"buyer": buyer_name}, as_dict=True)

	if not stats or not stats[0].total_evaluations:
		return {
			"total_evaluations": 0,
			"avg_score": 0,
			"min_score": 0,
			"max_score": 0,
			"passed_count": 0,
			"failed_count": 0,
			"pass_rate": 0,
		}

	s = stats[0]
	total = cint(s.total_evaluations)
	return {
		"total_evaluations": total,
		"avg_score": round(flt(s.avg_score), 2),
		"min_score": round(flt(s.min_score), 2),
		"max_score": round(flt(s.max_score), 2),
		"passed_count": cint(s.passed_count),
		"failed_count": cint(s.failed_count),
		"pass_rate": round(safe_divide(cint(s.passed_count), total) * 100, 1),
	}


# ---------------------------------------------------------------------------
# Data retrieval helpers — Seller anonymous summary
# ---------------------------------------------------------------------------

def _get_anonymous_kpi_summary(buyer_name):
	"""Get an anonymized KPI score summary for a buyer.

	Returns high-level score information without exposing the buyer's
	identity. Used in the seller's view of customer performance.

	Args:
		buyer_name: Buyer Profile name.

	Returns:
		dict or None: Anonymized KPI summary, or None if no scores exist.
	"""
	score = frappe.db.get_value(
		"Buyer KPI Score Log",
		{
			"buyer": buyer_name,
			"status": "Finalized",
		},
		[
			"overall_score", "grade", "score_trend", "passing_status",
			"metrics_evaluated", "metrics_passing", "metrics_warning",
			"metrics_critical",
		],
		as_dict=True,
		order_by="calculation_date desc",
	)

	if not score:
		return None

	return {
		"overall_score": flt(score.overall_score),
		"grade": score.grade,
		"score_trend": score.score_trend,
		"passing_status": score.passing_status,
		"metrics_evaluated": cint(score.metrics_evaluated),
		"metrics_passing": cint(score.metrics_passing),
		"metrics_warning": cint(score.metrics_warning),
		"metrics_critical": cint(score.metrics_critical),
	}


# ---------------------------------------------------------------------------
# Data retrieval helpers — Admin platform dashboard
# ---------------------------------------------------------------------------

def _get_platform_summary(tenant=None, period=None):
	"""Get platform-wide KPI summary statistics.

	Args:
		tenant: Optional tenant filter.
		period: Optional period filter (e.g., "2024-01").

	Returns:
		dict: Platform summary with total buyers, avg score, etc.
	"""
	params = {}
	conditions = ["status = 'Finalized'"]

	if tenant:
		conditions.append("tenant = %(tenant)s")
		params["tenant"] = tenant

	if period:
		conditions.append("score_period = %(period)s")
		params["period"] = period

	where_clause = " AND ".join(conditions)

	stats = frappe.db.sql("""
		SELECT
			COUNT(DISTINCT buyer) as total_buyers_scored,
			COUNT(*) as total_evaluations,
			AVG(overall_score) as avg_score,
			MIN(overall_score) as min_score,
			MAX(overall_score) as max_score,
			AVG(CASE WHEN score_change != 0 THEN score_change ELSE NULL END) as avg_score_change,
			SUM(CASE WHEN passing_status = 'Passed' THEN 1 ELSE 0 END) as passed_count,
			SUM(CASE WHEN passing_status = 'Failed' THEN 1 ELSE 0 END) as failed_count
		FROM `tabBuyer KPI Score Log`
		WHERE {where_clause}
	""".format(where_clause=where_clause), params, as_dict=True)

	if not stats or not stats[0].total_evaluations:
		return {
			"total_buyers_scored": 0,
			"total_evaluations": 0,
			"avg_score": 0,
			"min_score": 0,
			"max_score": 0,
			"avg_score_change": 0,
			"passed_count": 0,
			"failed_count": 0,
			"pass_rate": 0,
		}

	s = stats[0]
	total = cint(s.total_evaluations)
	return {
		"total_buyers_scored": cint(s.total_buyers_scored),
		"total_evaluations": total,
		"avg_score": round(flt(s.avg_score), 2),
		"min_score": round(flt(s.min_score), 2),
		"max_score": round(flt(s.max_score), 2),
		"avg_score_change": round(flt(s.avg_score_change), 2),
		"passed_count": cint(s.passed_count),
		"failed_count": cint(s.failed_count),
		"pass_rate": round(safe_divide(cint(s.passed_count), total) * 100, 1),
	}


def _get_grade_distribution(tenant=None, period=None):
	"""Get grade distribution across the platform.

	Args:
		tenant: Optional tenant filter.
		period: Optional period filter.

	Returns:
		dict: Grade distribution with counts for A-F.
	"""
	params = {}
	conditions = ["status = 'Finalized'", "grade IS NOT NULL", "grade != ''"]

	if tenant:
		conditions.append("tenant = %(tenant)s")
		params["tenant"] = tenant

	if period:
		conditions.append("score_period = %(period)s")
		params["period"] = period

	where_clause = " AND ".join(conditions)

	# Get grade counts — only from the latest score per buyer
	distribution = frappe.db.sql("""
		SELECT grade, COUNT(*) as count
		FROM (
			SELECT buyer, grade,
				ROW_NUMBER() OVER (PARTITION BY buyer ORDER BY calculation_date DESC) as rn
			FROM `tabBuyer KPI Score Log`
			WHERE {where_clause}
		) latest
		WHERE rn = 1
		GROUP BY grade
		ORDER BY grade
	""".format(where_clause=where_clause), params, as_dict=True)

	# Build complete distribution with all grades
	grade_map = {row.grade: cint(row.count) for row in distribution}
	return {
		"A": grade_map.get("A", 0),
		"B": grade_map.get("B", 0),
		"C": grade_map.get("C", 0),
		"D": grade_map.get("D", 0),
		"E": grade_map.get("E", 0),
		"F": grade_map.get("F", 0),
		"total": sum(grade_map.values()),
	}


def _get_score_distribution(tenant=None, period=None):
	"""Get score distribution in ranges across the platform.

	Args:
		tenant: Optional tenant filter.
		period: Optional period filter.

	Returns:
		dict: Score distribution by range (0-20, 20-40, ..., 80-100).
	"""
	params = {}
	conditions = ["status = 'Finalized'"]

	if tenant:
		conditions.append("tenant = %(tenant)s")
		params["tenant"] = tenant

	if period:
		conditions.append("score_period = %(period)s")
		params["period"] = period

	where_clause = " AND ".join(conditions)

	# Only count latest score per buyer
	distribution = frappe.db.sql("""
		SELECT
			SUM(CASE WHEN overall_score >= 90 THEN 1 ELSE 0 END) as excellent,
			SUM(CASE WHEN overall_score >= 70 AND overall_score < 90 THEN 1 ELSE 0 END) as good,
			SUM(CASE WHEN overall_score >= 50 AND overall_score < 70 THEN 1 ELSE 0 END) as average,
			SUM(CASE WHEN overall_score >= 30 AND overall_score < 50 THEN 1 ELSE 0 END) as below_average,
			SUM(CASE WHEN overall_score < 30 THEN 1 ELSE 0 END) as poor
		FROM (
			SELECT buyer, overall_score,
				ROW_NUMBER() OVER (PARTITION BY buyer ORDER BY calculation_date DESC) as rn
			FROM `tabBuyer KPI Score Log`
			WHERE {where_clause}
		) latest
		WHERE rn = 1
	""".format(where_clause=where_clause), params, as_dict=True)

	if not distribution:
		return {
			"excellent": 0,
			"good": 0,
			"average": 0,
			"below_average": 0,
			"poor": 0,
		}

	d = distribution[0]
	return {
		"excellent": cint(d.excellent),
		"good": cint(d.good),
		"average": cint(d.average),
		"below_average": cint(d.below_average),
		"poor": cint(d.poor),
	}


def _get_trend_data(tenant=None):
	"""Get period-over-period trend data for the platform.

	Returns average scores per period for the last 6 periods.

	Args:
		tenant: Optional tenant filter.

	Returns:
		list: List of dicts with period, avg_score, buyer_count, etc.
	"""
	params = {}
	conditions = ["status = 'Finalized'", "score_period IS NOT NULL", "score_period != ''"]

	if tenant:
		conditions.append("tenant = %(tenant)s")
		params["tenant"] = tenant

	where_clause = " AND ".join(conditions)

	trends = frappe.db.sql("""
		SELECT
			score_period,
			COUNT(DISTINCT buyer) as buyer_count,
			AVG(overall_score) as avg_score,
			MIN(overall_score) as min_score,
			MAX(overall_score) as max_score,
			SUM(CASE WHEN passing_status = 'Passed' THEN 1 ELSE 0 END) as passed_count,
			COUNT(*) as total_count
		FROM `tabBuyer KPI Score Log`
		WHERE {where_clause}
		GROUP BY score_period
		ORDER BY score_period DESC
		LIMIT 6
	""".format(where_clause=where_clause), params, as_dict=True)

	# Reverse to chronological order for chart rendering
	result = []
	for t in reversed(trends):
		total = cint(t.total_count)
		result.append({
			"period": t.score_period,
			"buyer_count": cint(t.buyer_count),
			"avg_score": round(flt(t.avg_score), 2),
			"min_score": round(flt(t.min_score), 2),
			"max_score": round(flt(t.max_score), 2),
			"pass_rate": round(safe_divide(cint(t.passed_count), total) * 100, 1),
		})

	return result


def _get_top_performers(tenant=None, limit=10):
	"""Get top-performing buyers by score.

	Args:
		tenant: Optional tenant filter.
		limit: Maximum number of results.

	Returns:
		list: Top performers with buyer info and score.
	"""
	params = {}
	conditions = ["bp.status = 'Active'", "bp.buyer_score > 0"]

	if tenant:
		conditions.append("bp.tenant = %(tenant)s")
		params["tenant"] = tenant

	where_clause = " AND ".join(conditions)

	top = frappe.db.sql("""
		SELECT
			bp.name as buyer,
			bp.buyer_name,
			bp.buyer_score,
			bp.buyer_score_trend,
			bp.buyer_level,
			bp.total_orders,
			bp.total_spent
		FROM `tabBuyer Profile` bp
		WHERE {where_clause}
		ORDER BY bp.buyer_score DESC
		LIMIT %(limit)s
	""".format(where_clause=where_clause), {**params, "limit": cint(limit)}, as_dict=True)

	return top


def _get_at_risk_buyers(tenant=None, limit=10):
	"""Get buyers with declining scores or low performance.

	Returns buyers whose score trend is declining or whose score
	is below the passing threshold.

	Args:
		tenant: Optional tenant filter.
		limit: Maximum number of results.

	Returns:
		list: At-risk buyers with buyer info, score, and trend.
	"""
	params = {}
	conditions = [
		"bp.status = 'Active'",
		"bp.buyer_score > 0",
		"(bp.buyer_score_trend = 'Down' OR bp.buyer_score < 60)",
	]

	if tenant:
		conditions.append("bp.tenant = %(tenant)s")
		params["tenant"] = tenant

	where_clause = " AND ".join(conditions)

	at_risk = frappe.db.sql("""
		SELECT
			bp.name as buyer,
			bp.buyer_name,
			bp.buyer_score,
			bp.buyer_score_trend,
			bp.buyer_level,
			bp.total_orders,
			bp.last_score_date
		FROM `tabBuyer Profile` bp
		WHERE {where_clause}
		ORDER BY bp.buyer_score ASC
		LIMIT %(limit)s
	""".format(where_clause=where_clause), {**params, "limit": cint(limit)}, as_dict=True)

	return at_risk
