import frappe


# Buyer KPI Template: 9 metrics, weights sum to 100
BUYER_KPI_METRICS = [
	{
		"metric_name": "Total Spend",
		"metric_code": "TOTAL_SPEND",
		"metric_type": "Sum",
		"weight": 20,
		"is_active": 1,
		"threshold_type": "Higher is Better",
		"target_value": 10000.0,
		"warning_threshold": 2000.0,
		"critical_threshold": 500.0,
		"scoring_method": "Logarithmic",
		"max_score": 100,
		"invert_score": 0,
		"description": "Total amount spent by the buyer across all orders",
	},
	{
		"metric_name": "Order Frequency",
		"metric_code": "ORDER_FREQ",
		"metric_type": "Rate",
		"weight": 15,
		"is_active": 1,
		"threshold_type": "Higher is Better",
		"target_value": 4.0,
		"warning_threshold": 1.5,
		"critical_threshold": 0.5,
		"scoring_method": "Linear",
		"max_score": 100,
		"invert_score": 0,
		"description": "Average number of orders placed per month",
	},
	{
		"metric_name": "Payment On-Time Rate",
		"metric_code": "PAYMENT_ON_TIME",
		"metric_type": "Percentage",
		"weight": 15,
		"is_active": 1,
		"threshold_type": "Higher is Better",
		"target_value": 100.0,
		"warning_threshold": 80.0,
		"critical_threshold": 50.0,
		"scoring_method": "Linear",
		"max_score": 100,
		"invert_score": 0,
		"description": "Percentage of payments made on or before due date",
	},
	{
		"metric_name": "Buyer Return Rate",
		"metric_code": "BUYER_RETURN_RATE",
		"metric_type": "Percentage",
		"weight": 10,
		"is_active": 1,
		"threshold_type": "Lower is Better",
		"target_value": 0.0,
		"warning_threshold": 10.0,
		"critical_threshold": 20.0,
		"scoring_method": "Linear",
		"max_score": 100,
		"invert_score": 1,
		"description": "Percentage of orders returned by the buyer",
	},
	{
		"metric_name": "Average Order Value",
		"metric_code": "AVG_ORDER_VAL",
		"metric_type": "Average",
		"weight": 10,
		"is_active": 1,
		"threshold_type": "Higher is Better",
		"target_value": 1000.0,
		"warning_threshold": 300.0,
		"critical_threshold": 100.0,
		"scoring_method": "Linear",
		"max_score": 100,
		"invert_score": 0,
		"description": "Average monetary value per order placed",
	},
	{
		"metric_name": "Buyer Dispute Rate",
		"metric_code": "BUYER_DISPUTE_RATE",
		"metric_type": "Percentage",
		"weight": 10,
		"is_active": 1,
		"threshold_type": "Lower is Better",
		"target_value": 0.0,
		"warning_threshold": 5.0,
		"critical_threshold": 10.0,
		"scoring_method": "Linear",
		"max_score": 100,
		"invert_score": 1,
		"description": "Percentage of orders resulting in disputes filed by the buyer",
	},
	{
		"metric_name": "Feedback Rate",
		"metric_code": "FEEDBACK_RATE",
		"metric_type": "Percentage",
		"weight": 5,
		"is_active": 1,
		"threshold_type": "Higher is Better",
		"target_value": 100.0,
		"warning_threshold": 50.0,
		"critical_threshold": 20.0,
		"scoring_method": "Linear",
		"max_score": 100,
		"invert_score": 0,
		"description": "Percentage of completed orders for which the buyer left feedback",
	},
	{
		"metric_name": "Account Age",
		"metric_code": "ACCOUNT_AGE",
		"metric_type": "Count",
		"weight": 10,
		"is_active": 1,
		"threshold_type": "Higher is Better",
		"target_value": 730.0,
		"warning_threshold": 180.0,
		"critical_threshold": 30.0,
		"scoring_method": "Linear",
		"max_score": 100,
		"invert_score": 0,
		"description": "Number of days since buyer account registration",
	},
	{
		"metric_name": "Last Activity",
		"metric_code": "LAST_ACTIVITY",
		"metric_type": "Count",
		"weight": 5,
		"is_active": 1,
		"threshold_type": "Lower is Better",
		"target_value": 7.0,
		"warning_threshold": 30.0,
		"critical_threshold": 90.0,
		"scoring_method": "Linear",
		"max_score": 100,
		"invert_score": 1,
		"description": "Number of days since the buyer last placed an order or interacted",
	},
]


def execute():
	"""Idempotent seed script for the default Buyer KPI Template record.

	Creates one Buyer KPI Template record in the Buyer KPI Template DocType:
	  - STD_BUYER_KPI (9 metrics, total weight=100)

	Safe to run multiple times — skips creation if a template with the
	same name already exists.
	"""
	template_name = "Standard Buyer KPI"

	if frappe.db.exists("Buyer KPI Template", template_name):
		return

	doc = frappe.new_doc("Buyer KPI Template")
	doc.template_name = template_name
	doc.template_code = "STD_BUYER_KPI"
	doc.status = "Active"
	doc.is_default = 1
	doc.passing_score = 60
	doc.scoring_curve = "Linear"
	doc.display_name = "Standard Buyer KPI"
	doc.description = "Default KPI template for evaluating buyer performance across 9 key metrics"

	for metric_data in BUYER_KPI_METRICS:
		doc.append("metrics", metric_data)

	doc.total_weight = 100
	doc.insert(ignore_permissions=True)
	frappe.db.commit()
