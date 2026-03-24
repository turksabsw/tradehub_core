# Copyright (c) 2024, TradeHub Team and contributors
# For license information, please see license.txt

"""
Auto-match utilities for smart column mapping in import jobs.

Provides Turkish-aware column matching with 4 priority levels:
- P0: Historical (100%) — Previously confirmed mappings from Mapping History
- P1: Exact (100%) — Exact string match against target field labels/names
- P2: Normalized (90%) — Turkish-normalized case-insensitive match
- P3: Fuzzy (70%+) — difflib.SequenceMatcher with threshold 0.7

Also provides functions for saving confirmed mappings to history
and applying mapping templates to import jobs.
"""

import json
from difflib import SequenceMatcher

import frappe
from frappe import _
from frappe.utils import now_datetime


def normalize_turkish(text):
    """
    Normalize text with Turkish-specific character handling.

    Handles Turkish I/İ/ı/i mapping correctly:
    - İ → i (Turkish capital I with dot → lowercase i)
    - I → ı (Turkish capital I without dot → lowercase ı)
    - ı → ı (Turkish lowercase dotless i stays)
    - i → i (regular lowercase i stays)

    Also normalizes Ş→ş, Ç→ç, Ğ→ğ, Ö→ö, Ü→ü, strips whitespace,
    and collapses separators (underscores, hyphens) into single spaces.

    Args:
        text (str): The text to normalize.

    Returns:
        str: Normalized lowercase text with Turkish character mapping applied.

    Example:
        >>> normalize_turkish("İstanbul")
        'istanbul'
        >>> normalize_turkish("IĞDIR")
        'ığdır'
        >>> normalize_turkish("Ürün_Adı")
        'ürün adı'
    """
    if not text:
        return ""

    # Turkish-specific lowercase mapping
    turkish_map = str.maketrans({
        "İ": "i",
        "I": "ı",
        "Ş": "ş",
        "Ç": "ç",
        "Ğ": "ğ",
        "Ö": "ö",
        "Ü": "ü",
    })

    result = text.translate(turkish_map).lower()
    # Remove extra whitespace, underscores, hyphens for normalization
    result = result.strip().replace("_", " ").replace("-", " ")
    # Collapse multiple spaces
    result = " ".join(result.split())
    return result


@frappe.whitelist()
def auto_match_columns(source_columns, target_doctype, import_type, tenant=None, seller=None):
    """
    Auto-match source columns to target DocType fields using 4-priority matching.

    Priority levels (highest to lowest):
    - P0 Historical (100%): Matches from Mapping History for same context
    - P1 Exact (100%): Exact string match on target field label or fieldname
    - P2 Normalized (90%): Turkish-normalized match on label or fieldname
    - P3 Fuzzy (70%+): difflib.SequenceMatcher with threshold 0.7

    Uses a ``used_targets`` set to prevent duplicate target assignments —
    once a target field is matched, it cannot be assigned to another source column.

    Args:
        source_columns (list[str]): List of column names from the import file.
        target_doctype (str): The target DocType name (e.g., "Listing").
        import_type (str): The import type (e.g., "Listing", "SKU").
        tenant (str, optional): Tenant name for context-scoped history lookup.
        seller (str, optional): Seller name for context-scoped history lookup.

    Returns:
        list[dict]: List of match result dicts, one per source column, each containing:
            - ``source_column`` (str): Original source column name
            - ``target_fieldname`` (str or None): Matched target field name
            - ``target_label`` (str or None): Human-readable label of target field
            - ``confidence`` (int): Match confidence 0-100
            - ``match_strategy`` (str): One of "Historical", "Exact", "Normalized", "Fuzzy", "None"

    Example:
        >>> results = auto_match_columns(
        ...     ["Ürün Adı", "Fiyat", "SKU Kodu"],
        ...     "Listing",
        ...     "Listing",
        ...     tenant="TENANT-001"
        ... )
    """
    # Handle JSON string from frappe.call
    if isinstance(source_columns, str):
        source_columns = json.loads(source_columns)

    if not source_columns or not target_doctype:
        return []

    # Get target fields from DocType meta
    target_fields = _get_target_fields(target_doctype)
    if not target_fields:
        return []

    # Get historical mappings for this context
    historical_mappings = _get_historical_mappings(
        target_doctype, import_type, tenant, seller
    )

    # Track which target fields have been assigned to prevent duplicates
    used_targets = set()
    results = []

    for source_col in source_columns:
        match = _match_single_column(
            source_col,
            target_fields,
            historical_mappings,
            used_targets
        )
        results.append(match)

        # Mark target as used if matched
        if match["target_fieldname"]:
            used_targets.add(match["target_fieldname"])

    return results


@frappe.whitelist()
def save_to_mapping_history(confirmed_mappings, target_doctype, import_type,
                            tenant=None, seller=None):
    """
    Save confirmed column mappings to Mapping History for future P0 lookups.

    For each confirmed mapping, either creates a new Mapping History record
    or increments the confirmation count on an existing one.

    Args:
        confirmed_mappings (list[dict]): List of confirmed mappings, each with:
            - ``source_column`` (str): Source column name
            - ``target_fieldname`` (str): Confirmed target field name
        target_doctype (str): The target DocType name.
        import_type (str): The import type.
        tenant (str, optional): Tenant name for scoping.
        seller (str, optional): Seller name for scoping.

    Returns:
        int: Number of mappings saved or updated.

    Example:
        >>> count = save_to_mapping_history(
        ...     [{"source_column": "Ürün Adı", "target_fieldname": "listing_title"}],
        ...     "Listing", "Listing", tenant="TENANT-001"
        ... )
    """
    # Handle JSON string from frappe.call
    if isinstance(confirmed_mappings, str):
        confirmed_mappings = json.loads(confirmed_mappings)

    if not confirmed_mappings:
        return 0

    saved_count = 0

    for mapping in confirmed_mappings:
        source_col = mapping.get("source_column")
        target_field = mapping.get("target_fieldname")

        if not source_col or not target_field:
            continue

        # Check if a history record already exists for this exact mapping
        existing = _find_existing_history(
            source_col, target_field, target_doctype, import_type, tenant, seller
        )

        if existing:
            # Increment confirmation count on existing record
            history_doc = frappe.get_doc("Mapping History", existing)
            history_doc.increment_confirmation()
        else:
            # Create new history record
            history_doc = frappe.new_doc("Mapping History")
            history_doc.source_column = source_col
            history_doc.source_column_normalized = normalize_turkish(source_col)
            history_doc.target_fieldname = target_field
            history_doc.target_doctype = target_doctype
            history_doc.import_type = import_type
            history_doc.tenant = tenant
            history_doc.seller = seller
            history_doc.confirmation_count = 1
            history_doc.last_confirmed = now_datetime()
            history_doc.insert(ignore_permissions=True)

        saved_count += 1

    return saved_count


def apply_mapping_template(import_job_name, template_name):
    """
    Apply a mapping template to an import job by setting its column_mapping field.

    Reads the template's field definitions and converts them into the
    JSON column_mapping format expected by the Import Job DocType.

    Args:
        import_job_name (str): The name of the Import Job document.
        template_name (str): The name of the Mapping Template to apply.

    Returns:
        dict: The column mapping dict that was applied.

    Raises:
        frappe.DoesNotExistError: If import job or template does not exist.
        frappe.ValidationError: If import job is not in Pending status.

    Example:
        >>> mapping = apply_mapping_template("IMP-2024-00001", "My Listing Template")
    """
    # Validate import job exists
    if not frappe.db.exists("Import Job", import_job_name):
        frappe.throw(
            _("Import Job {0} does not exist").format(import_job_name),
            frappe.DoesNotExistError
        )

    # Validate template exists
    if not frappe.db.exists("Mapping Template", template_name):
        frappe.throw(
            _("Mapping Template {0} does not exist").format(template_name),
            frappe.DoesNotExistError
        )

    import_job = frappe.get_doc("Import Job", import_job_name)

    # Only allow applying template when job is Pending
    if import_job.status != "Pending":
        frappe.throw(
            _("Mapping template can only be applied when Import Job status is Pending. "
              "Current status: {0}").format(import_job.status),
            frappe.ValidationError
        )

    template = frappe.get_doc("Mapping Template", template_name)

    # Build column mapping from template fields
    column_mapping = {}
    for field in template.fields:
        column_mapping[field.source_column] = field.target_fieldname

    # Apply to import job
    import_job.column_mapping = json.dumps(
        column_mapping, indent=2, ensure_ascii=False, default=str
    )
    import_job.save(ignore_permissions=True)

    return column_mapping


@frappe.whitelist()
def auto_match_for_import_job(import_job_name):
    """
    Run auto-match for an Import Job by reading its file headers and matching them.

    This is the primary API endpoint called from the Import Job form's
    "Auto-Match Columns" button. It reads the import file headers,
    determines the target DocType from the import type, and runs the
    4-priority auto-match algorithm.

    Args:
        import_job_name (str): Name of the Import Job document.

    Returns:
        dict: Dictionary containing:
            - ``headers`` (list[str]): Source column headers from the file
            - ``matches`` (list[dict]): Auto-match results per column
            - ``target_doctype`` (str): Resolved target DocType name
            - ``import_type`` (str): Import type from the job

    Raises:
        frappe.ValidationError: If job status is not Pending or no headers found.
    """
    job = frappe.get_doc("Import Job", import_job_name)

    if job.status != "Pending":
        frappe.throw(
            _("Auto-match is only available when Import Job status is Pending"),
            frappe.ValidationError
        )

    if not job.import_file:
        frappe.throw(
            _("No import file attached to this job"),
            frappe.ValidationError
        )

    rows, headers = job.read_import_file()
    if not headers:
        frappe.throw(_("No column headers found in the import file"))

    # Resolve target DocType from import type
    target_doctype = _get_target_doctype_for_import(job.import_type)

    results = auto_match_columns(
        headers, target_doctype, job.import_type,
        tenant=job.tenant, seller=job.seller
    )

    return {
        "headers": headers,
        "matches": results,
        "target_doctype": target_doctype,
        "import_type": job.import_type
    }


def _get_target_doctype_for_import(import_type):
    """
    Resolve the target DocType from an import type string.

    Args:
        import_type (str): The import type (e.g., "Listing", "SKU").

    Returns:
        str: The target DocType name.
    """
    doctype_map = {
        "Listing": "Listing",
        "Listing Variant": "Listing Variant",
        "SKU": "SKU",
        "Category": "Category",
        "Media Asset": "Media Asset",
        "Inventory Update": "Listing",
        "Price Update": "Listing",
    }
    return doctype_map.get(import_type, import_type)


# ---------------------------------------------------------------------------
# Internal helper functions
# ---------------------------------------------------------------------------


def _get_target_fields(doctype):
    """
    Get all mappable fields from a DocType's metadata.

    Returns fields that have data-holding fieldtypes (excludes layout fields
    like Section Break, Column Break, Tab Break, etc.).

    Args:
        doctype (str): The DocType name.

    Returns:
        list[dict]: List of field dicts with ``fieldname``, ``label``, and
            ``label_normalized`` keys.
    """
    try:
        meta = frappe.get_meta(doctype)
    except Exception:
        return []

    # Fieldtypes that are not data-holding (layout/structural)
    non_data_fieldtypes = {
        "Section Break", "Column Break", "Tab Break",
        "HTML", "Heading", "Button", "Fold",
    }

    fields = []
    for field in meta.fields:
        if field.fieldtype in non_data_fieldtypes:
            continue

        label = field.label or field.fieldname
        fields.append({
            "fieldname": field.fieldname,
            "label": label,
            "label_normalized": normalize_turkish(label),
            "fieldname_normalized": normalize_turkish(field.fieldname),
        })

    return fields


def _get_historical_mappings(target_doctype, import_type, tenant=None, seller=None):
    """
    Fetch historical mappings from Mapping History for the given context.

    Returns mappings ordered by confirmation_count (descending) so the
    most frequently confirmed mappings take priority.

    Args:
        target_doctype (str): The target DocType.
        import_type (str): The import type.
        tenant (str, optional): Tenant scope filter.
        seller (str, optional): Seller scope filter.

    Returns:
        list[dict]: List of history records with source_column,
            source_column_normalized, and target_fieldname.
    """
    filters = {
        "target_doctype": target_doctype,
        "import_type": import_type,
    }

    if tenant:
        filters["tenant"] = tenant
    if seller:
        filters["seller"] = seller

    return frappe.get_all(
        "Mapping History",
        filters=filters,
        fields=["source_column", "source_column_normalized", "target_fieldname"],
        order_by="confirmation_count desc, last_confirmed desc",
    )


def _match_single_column(source_col, target_fields, historical_mappings, used_targets):
    """
    Match a single source column against target fields using 4-priority matching.

    Args:
        source_col (str): The source column name to match.
        target_fields (list[dict]): Available target fields.
        historical_mappings (list[dict]): Historical mapping records.
        used_targets (set): Set of already-assigned target fieldnames.

    Returns:
        dict: Match result with source_column, target_fieldname, target_label,
            confidence, and match_strategy.
    """
    no_match = {
        "source_column": source_col,
        "target_fieldname": None,
        "target_label": None,
        "confidence": 0,
        "match_strategy": "None",
    }

    source_normalized = normalize_turkish(source_col)

    # P0: Historical match (100% confidence)
    for hist in historical_mappings:
        if hist["target_fieldname"] in used_targets:
            continue

        hist_source_normalized = hist.get("source_column_normalized") or normalize_turkish(
            hist["source_column"]
        )

        if source_normalized == hist_source_normalized:
            # Verify target still exists in the DocType
            target_label = _get_target_label(hist["target_fieldname"], target_fields)
            if target_label is not None:
                return {
                    "source_column": source_col,
                    "target_fieldname": hist["target_fieldname"],
                    "target_label": target_label,
                    "confidence": 100,
                    "match_strategy": "Historical",
                }

    # P1: Exact match (100% confidence)
    for field in target_fields:
        if field["fieldname"] in used_targets:
            continue

        if source_col == field["label"] or source_col == field["fieldname"]:
            return {
                "source_column": source_col,
                "target_fieldname": field["fieldname"],
                "target_label": field["label"],
                "confidence": 100,
                "match_strategy": "Exact",
            }

    # P2: Normalized match (90% confidence)
    for field in target_fields:
        if field["fieldname"] in used_targets:
            continue

        if source_normalized == field["label_normalized"] or \
           source_normalized == field["fieldname_normalized"]:
            return {
                "source_column": source_col,
                "target_fieldname": field["fieldname"],
                "target_label": field["label"],
                "confidence": 90,
                "match_strategy": "Normalized",
            }

    # P3: Fuzzy match (70%+ confidence)
    best_match = None
    best_ratio = 0.0

    for field in target_fields:
        if field["fieldname"] in used_targets:
            continue

        # Compare normalized source against normalized label and fieldname
        ratio_label = SequenceMatcher(
            None, source_normalized, field["label_normalized"]
        ).ratio()
        ratio_fieldname = SequenceMatcher(
            None, source_normalized, field["fieldname_normalized"]
        ).ratio()

        ratio = max(ratio_label, ratio_fieldname)

        if ratio >= 0.7 and ratio > best_ratio:
            best_ratio = ratio
            best_match = field

    if best_match:
        return {
            "source_column": source_col,
            "target_fieldname": best_match["fieldname"],
            "target_label": best_match["label"],
            "confidence": int(best_ratio * 100),
            "match_strategy": "Fuzzy",
        }

    return no_match


def _get_target_label(fieldname, target_fields):
    """
    Look up the label for a target fieldname.

    Args:
        fieldname (str): The field name to look up.
        target_fields (list[dict]): Available target fields.

    Returns:
        str or None: The field label if found, None otherwise.
    """
    for field in target_fields:
        if field["fieldname"] == fieldname:
            return field["label"]
    return None


def _find_existing_history(source_col, target_field, target_doctype,
                           import_type, tenant=None, seller=None):
    """
    Find an existing Mapping History record for the given mapping context.

    Args:
        source_col (str): Source column name.
        target_field (str): Target fieldname.
        target_doctype (str): Target DocType.
        import_type (str): Import type.
        tenant (str, optional): Tenant scope.
        seller (str, optional): Seller scope.

    Returns:
        str or None: Name of existing Mapping History record, or None.
    """
    filters = {
        "source_column_normalized": normalize_turkish(source_col),
        "target_fieldname": target_field,
        "target_doctype": target_doctype,
        "import_type": import_type,
    }

    if tenant:
        filters["tenant"] = tenant
    else:
        filters["tenant"] = ["is", "not set"]

    if seller:
        filters["seller"] = seller
    else:
        filters["seller"] = ["is", "not set"]

    return frappe.db.exists("Mapping History", filters)
