# Copyright (c) 2024, TradeHub Team and contributors
# For license information, please see license.txt

"""
ECA (Event-Condition-Action) Rule Dispatcher.
Evaluates and executes ECA rules based on document events.

The ECA system allows defining rules that:
- Trigger on specific DocType events (e.g., on_submit, on_update)
- Evaluate conditions against document fields
- Execute actions (email, webhook, field updates, etc.)
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, now_datetime


def evaluate_rules(doc, method=None):
    """
    Evaluate all active ECA rules for a document event.

    This is the main entry point for ECA rule processing.
    Called from doc_events hooks for each document operation.

    Args:
        doc: The Frappe document that triggered the event.
        method (str, optional): The doc event method name
            (e.g., 'on_submit', 'on_update', 'before_save').

    Example:
        # In hooks.py
        doc_events = {
            "*": {
                "on_update": "tradehub_core.eca.dispatcher.evaluate_rules",
                "on_submit": "tradehub_core.eca.dispatcher.evaluate_rules",
            }
        }
    """
    if not method:
        return

    # Anti-recursion: Skip system log doctypes
    if doc.doctype in ["Error Log", "Log", "Console Log", "Access Log", "Activity Log", "ECA Rule Log", "Prepared Report", "Version", "Scheduled Job Log"]:
        return

    # Skip if ECA is disabled globally
    if not _is_eca_enabled():
        return

    # Get applicable rules for this DocType and event
    rules = _get_applicable_rules(doc.doctype, method)

    if not rules:
        return

    for rule in rules:
        try:
            _process_rule(doc, rule, method)
        except Exception as e:
            _log_rule_error(rule, doc, method, e)


def _is_eca_enabled():
    """
    Check if ECA rule processing is enabled globally.

    Returns:
        bool: True if ECA is enabled, False otherwise.
    """
    try:
        return cint(frappe.db.get_single_value("Analytics Settings", "enable_eca_rules"))
    except Exception:
        # Prevent recursion by suppressing error logging here
        return 0


def _get_applicable_rules(doctype, event):
    """
    Get all active ECA rules applicable to a DocType and event.

    Args:
        doctype (str): The DocType name.
        event (str): The event name (e.g., 'on_update', 'on_submit').

    Returns:
        list: List of ECA Rule documents.
    """
    cache_key = f"eca_rules:{doctype}:{event}"
    rules = frappe.cache().get_value(cache_key)

    if rules is None:
        rules = frappe.get_all(
            "ECA Rule",
            filters={
                "doctype_name": doctype,
                "event": event,
                "enabled": 1
            },
            fields=["name", "condition", "action_type", "action_template", "priority"],
            order_by="priority asc"
        )
        # Cache for 5 minutes
        frappe.cache().set_value(cache_key, rules, expires_in_sec=300)

    return rules


def _process_rule(doc, rule, event):
    """
    Process a single ECA rule against a document.

    Args:
        doc: The document to evaluate.
        rule (dict): The ECA Rule data.
        event (str): The event name.
    """
    # Load full rule document for detailed processing
    rule_doc = frappe.get_cached_doc("ECA Rule", rule.name)

    # Evaluate condition
    if not _evaluate_condition(doc, rule_doc):
        return

    # Execute action
    _execute_action(doc, rule_doc, event)

    # Log successful execution
    _log_rule_execution(rule_doc, doc, event, success=True)


def _evaluate_condition(doc, rule):
    """
    Evaluate the condition expression of an ECA rule.

    Args:
        doc: The document to evaluate against.
        rule: The ECA Rule document.

    Returns:
        bool: True if condition is met, False otherwise.
    """
    condition = rule.get("condition")

    # No condition means always execute
    if not condition or condition.strip() == "":
        return True

    try:
        # Create evaluation context with document data
        context = _get_evaluation_context(doc)

        # Evaluate the condition
        result = frappe.safe_eval(condition, context)
        return bool(result)

    except Exception as e:
        frappe.log_error(
            message=f"ECA Rule condition evaluation failed: {e}\nCondition: {condition}",
            title=f"ECA Rule Error: {rule.name}"
        )
        return False


def _get_evaluation_context(doc):
    """
    Build the evaluation context for condition expressions.

    Args:
        doc: The document being evaluated.

    Returns:
        dict: Context dictionary for safe_eval.
    """
    return {
        "doc": doc.as_dict(),
        "frappe": frappe._dict({
            "utils": frappe._dict({
                "getdate": getdate,
                "now_datetime": now_datetime,
                "cint": cint,
                "flt": flt
            }),
            "session": frappe._dict({
                "user": frappe.session.user
            })
        }),
        "True": True,
        "False": False,
        "None": None
    }


def _execute_action(doc, rule, event):
    """
    Execute the action defined in an ECA rule.

    Args:
        doc: The document that triggered the rule.
        rule: The ECA Rule document.
        event (str): The event name.
    """
    action_type = rule.get("action_type")

    if action_type == "Email":
        _execute_email_action(doc, rule)
    elif action_type == "Webhook":
        _execute_webhook_action(doc, rule)
    elif action_type == "Field Update":
        _execute_field_update_action(doc, rule)
    elif action_type == "Custom Script":
        _execute_custom_script_action(doc, rule)
    elif action_type == "Create Document":
        _execute_create_document_action(doc, rule)
    else:
        frappe.log_error(
            message=f"Unknown action type: {action_type}",
            title=f"ECA Rule Error: {rule.name}"
        )


def _execute_email_action(doc, rule):
    """
    Execute an email notification action.

    Args:
        doc: The source document.
        rule: The ECA Rule with email configuration.
    """
    template_name = rule.get("action_template")
    if not template_name:
        return

    try:
        template = frappe.get_cached_doc("ECA Action Template", template_name)

        # Build recipient list
        recipients = _get_recipients(doc, template)

        if not recipients:
            return

        # Render email content
        context = {"doc": doc.as_dict()}
        subject = frappe.render_template(template.get("subject") or "", context)
        message = frappe.render_template(template.get("message") or "", context)

        # Send email
        frappe.sendmail(
            recipients=recipients,
            subject=subject,
            message=message,
            reference_doctype=doc.doctype,
            reference_name=doc.name,
            now=True
        )

    except Exception as e:
        frappe.log_error(
            message=f"Email action failed: {e}",
            title=f"ECA Rule Email Error: {rule.name}"
        )


def _execute_webhook_action(doc, rule):
    """
    Execute a webhook action.

    Args:
        doc: The source document.
        rule: The ECA Rule with webhook configuration.
    """
    template_name = rule.get("action_template")
    if not template_name:
        return

    try:
        template = frappe.get_cached_doc("ECA Action Template", template_name)
        webhook_url = template.get("webhook_url")

        if not webhook_url:
            return

        import requests
        import json

        # Prepare payload
        payload = {
            "doctype": doc.doctype,
            "name": doc.name,
            "data": doc.as_dict(),
            "event": rule.get("event")
        }

        headers = {"Content-Type": "application/json"}

        # Add custom headers if specified
        custom_headers = template.get("webhook_headers")
        if custom_headers:
            try:
                headers.update(json.loads(custom_headers))
            except json.JSONDecodeError:
                pass

        # Make request in background
        frappe.enqueue(
            "tradehub_core.eca.dispatcher._send_webhook",
            queue="short",
            url=webhook_url,
            payload=payload,
            headers=headers
        )

    except Exception as e:
        frappe.log_error(
            message=f"Webhook action failed: {e}",
            title=f"ECA Rule Webhook Error: {rule.name}"
        )


def _send_webhook(url, payload, headers):
    """
    Send webhook request (called in background).

    Args:
        url (str): Webhook URL.
        payload (dict): Request payload.
        headers (dict): Request headers.
    """
    import requests
    import json

    try:
        response = requests.post(
            url,
            data=json.dumps(payload, default=str),
            headers=headers,
            timeout=30
        )
        response.raise_for_status()
    except Exception as e:
        frappe.log_error(
            message=f"Webhook request failed: {e}\nURL: {url}",
            title="ECA Webhook Error"
        )


def _execute_field_update_action(doc, rule):
    """
    Execute a field update action on the document.

    Args:
        doc: The document to update.
        rule: The ECA Rule with field update configuration.
    """
    template_name = rule.get("action_template")
    if not template_name:
        return

    try:
        template = frappe.get_cached_doc("ECA Action Template", template_name)
        updates = template.get("field_updates") or []

        for update in updates:
            fieldname = update.get("fieldname")
            value_type = update.get("value_type", "Static")
            value = update.get("value")

            if not fieldname:
                continue

            if value_type == "Expression":
                context = _get_evaluation_context(doc)
                value = frappe.safe_eval(value, context)

            # Update the document field
            doc.db_set(fieldname, value, update_modified=False)

    except Exception as e:
        frappe.log_error(
            message=f"Field update action failed: {e}",
            title=f"ECA Rule Field Update Error: {rule.name}"
        )


def _execute_custom_script_action(doc, rule):
    """
    Execute a custom Python script action.

    Args:
        doc: The source document.
        rule: The ECA Rule with custom script.
    """
    script = rule.get("custom_script")
    if not script:
        return

    try:
        context = _get_evaluation_context(doc)
        context["doc"] = doc  # Provide actual document object

        exec(script, context)

    except Exception as e:
        frappe.log_error(
            message=f"Custom script action failed: {e}\nScript: {script[:500]}",
            title=f"ECA Rule Script Error: {rule.name}"
        )


def _execute_create_document_action(doc, rule):
    """
    Execute a create document action.

    Args:
        doc: The source document.
        rule: The ECA Rule with document creation config.
    """
    template_name = rule.get("action_template")
    if not template_name:
        return

    try:
        template = frappe.get_cached_doc("ECA Action Template", template_name)
        target_doctype = template.get("target_doctype")

        if not target_doctype:
            return

        # Build new document from template
        new_doc_data = {"doctype": target_doctype}

        # Map fields from source to target
        field_mappings = template.get("field_mappings") or []
        for mapping in field_mappings:
            source_field = mapping.get("source_field")
            target_field = mapping.get("target_field")

            if source_field and target_field:
                value = doc.get(source_field)
                if value is not None:
                    new_doc_data[target_field] = value

        # Create and insert new document
        new_doc = frappe.get_doc(new_doc_data)
        new_doc.flags.ignore_permissions = True
        new_doc.insert()

    except Exception as e:
        frappe.log_error(
            message=f"Create document action failed: {e}",
            title=f"ECA Rule Create Doc Error: {rule.name}"
        )


def _get_recipients(doc, template):
    """
    Get email recipients based on template configuration.

    Args:
        doc: The source document.
        template: The ECA Action Template.

    Returns:
        list: List of email addresses.
    """
    recipients = []

    # Static recipients
    static_recipients = template.get("recipients")
    if static_recipients:
        recipients.extend([r.strip() for r in static_recipients.split(",") if r.strip()])

    # Dynamic recipients from document field
    recipient_field = template.get("recipient_field")
    if recipient_field and doc.get(recipient_field):
        field_value = doc.get(recipient_field)
        if isinstance(field_value, str):
            recipients.extend([r.strip() for r in field_value.split(",") if r.strip()])

    # Recipients from linked user/contact
    recipient_link_field = template.get("recipient_link_field")
    if recipient_link_field and doc.get(recipient_link_field):
        linked_user = doc.get(recipient_link_field)
        email = frappe.db.get_value("User", linked_user, "email")
        if email:
            recipients.append(email)

    return list(set(recipients))  # Remove duplicates


def _log_rule_execution(rule, doc, event, success=True):
    """
    Log ECA rule execution for auditing.

    Args:
        rule: The ECA Rule document.
        doc: The document that triggered the rule.
        event (str): The event name.
        success (bool): Whether execution was successful.
    """
    try:
        log = frappe.get_doc({
            "doctype": "ECA Rule Log",
            "eca_rule": rule.name,
            "reference_doctype": doc.doctype,
            "reference_name": doc.name,
            "event": event,
            "status": "Success" if success else "Failed",
            "execution_time": now_datetime()
        })
        log.flags.ignore_permissions = True
        log.insert()
    except Exception:
        pass  # Don't fail the main operation for logging issues


def _log_rule_error(rule, doc, event, error):
    """
    Log ECA rule execution error.

    Args:
        rule (dict): The ECA Rule data.
        doc: The document that triggered the rule.
        event (str): The event name.
        error: The exception that occurred.
    """
    frappe.log_error(
        message=f"ECA Rule execution failed\nRule: {rule.name}\nDocType: {doc.doctype}\nDoc: {doc.name}\nEvent: {event}\nError: {error}",
        title=f"ECA Rule Error: {rule.name}"
    )

    # Also log to ECA Rule Log
    try:
        log = frappe.get_doc({
            "doctype": "ECA Rule Log",
            "eca_rule": rule.name,
            "reference_doctype": doc.doctype,
            "reference_name": doc.name,
            "event": event,
            "status": "Failed",
            "error_message": str(error)[:2000],
            "execution_time": now_datetime()
        })
        log.flags.ignore_permissions = True
        log.insert()
    except Exception:
        pass


def clear_eca_cache(doctype=None, event=None):
    """
    Clear ECA rule cache.

    Args:
        doctype (str, optional): Specific DocType to clear cache for.
        event (str, optional): Specific event to clear cache for.
    """
    if doctype and event:
        frappe.cache().delete_value(f"eca_rules:{doctype}:{event}")
    elif doctype:
        # Clear all events for this DocType
        events = ["on_update", "on_submit", "on_cancel", "before_save", "after_insert"]
        for evt in events:
            frappe.cache().delete_value(f"eca_rules:{doctype}:{evt}")
    else:
        # Clear all ECA cache
        frappe.cache().delete_keys("eca_rules:*")
