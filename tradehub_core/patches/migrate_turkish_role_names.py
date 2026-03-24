# Copyright (c) 2026, Trade Hub and contributors
# For license information, please see license.txt

"""
Migration Patch: Standardize Turkish Role Names to English.

This patch renames legacy Turkish role names to their standardised English equivalents
across the entire Frappe site.  The mapping is:

    Marka Sahibi  ->  Seller Owner
    Satici Admin  ->  Seller Admin
    Satici        ->  Seller Staff
    Alici Admin   ->  Buyer Admin
    Alici Editor  ->  Buyer Procurement

The patch is **idempotent**: it skips roles that have already been renamed and
handles the case where old roles no longer exist in the database.

Affected tables:
    - tabRole             (role definition)
    - tabHas Role         (user-role assignments)
    - tabDocPerm          (DocType permission rules)
    - tabCustom DocPerm   (custom permission overrides)
    - tabRole Profile Role (role-profile member entries)
"""

import frappe
from frappe import _


# Mapping: old Turkish name -> new English name
# Order matters: longer/more-specific names first to avoid partial-match issues
# when scanning logs, but SQL uses exact match so order is safe here.
ROLE_NAME_MAP = {
    "Marka Sahibi": "Seller Owner",
    "Satici Admin": "Seller Admin",
    "Satici": "Seller Staff",
    "Alici Admin": "Buyer Admin",
    "Alici Editor": "Buyer Procurement",
}


def execute():
    """
    Main entry point for the migration patch.

    Renames Turkish role names to English across all relevant tables.
    """
    migrated = []
    skipped = []

    for old_name, new_name in ROLE_NAME_MAP.items():
        try:
            result = _rename_role(old_name, new_name)
            if result:
                migrated.append(f"{old_name} -> {new_name}")
            else:
                skipped.append(f"{old_name} (not found or already migrated)")
        except Exception:
            frappe.log_error(
                title=_("Role Migration Error"),
                message=_("Failed to rename role {0} to {1}").format(old_name, new_name)
            )
            raise

    if migrated:
        frappe.log_error(
            title=_("Role Migration Complete"),
            message=_("Migrated roles: {0}").format(", ".join(migrated))
        )

    if skipped:
        frappe.log_error(
            title=_("Role Migration Skipped"),
            message=_("Skipped roles: {0}").format(", ".join(skipped))
        )


def _rename_role(old_name, new_name):
    """
    Rename a single role from old_name to new_name across all relevant tables.

    Returns True if the role was renamed, False if it was already migrated or
    the old role does not exist.
    """
    # Check if the old role exists
    if not frappe.db.exists("Role", old_name):
        return False

    # Check if the new role already exists (created by fixtures before patch ran)
    new_role_exists = frappe.db.exists("Role", new_name)

    if new_role_exists:
        # New role already exists (e.g. from fixtures).
        # Migrate references from old role to new role, then delete the old role.
        _update_references(old_name, new_name)
        _delete_role(old_name)
    else:
        # Simply rename the old role record to the new name.
        # frappe.rename_doc handles updating child references automatically
        # for Link fields that point to Role.
        frappe.rename_doc("Role", old_name, new_name, force=True)

    frappe.db.commit()
    return True


def _update_references(old_name, new_name):
    """
    Update all foreign-key references from old_name to new_name in tables
    that store role names but are not automatically handled by frappe.rename_doc.

    This is used when the new role already exists (e.g. created by fixtures)
    and we need to migrate user assignments from the old role to the new one.
    """
    # 1. Has Role (user-role assignments)
    _update_table_role("Has Role", "role", old_name, new_name)

    # 2. DocPerm (DocType permission rules)
    _update_table_role("DocPerm", "role", old_name, new_name)

    # 3. Custom DocPerm (custom permission overrides)
    _update_table_role("Custom DocPerm", "role", old_name, new_name)

    # 4. Role Profile Role (role profile membership)
    _update_table_role("Role Profile Role", "role", old_name, new_name)


def _update_table_role(doctype, fieldname, old_name, new_name):
    """
    Update role references in a specific table, handling duplicates gracefully.

    If a user already has the new role assigned, we skip the duplicate rather than
    creating a constraint violation.
    """
    table_name = "tab" + doctype

    # Check if the table exists (some may not exist on fresh installs)
    if not frappe.db.table_exists(table_name):
        return

    if doctype == "Has Role":
        # For Has Role, avoid duplicate user-role pairs.
        # Delete old-role entries where the user already has the new role.
        frappe.db.sql("""
            DELETE hr_old FROM `tabHas Role` hr_old
            INNER JOIN `tabHas Role` hr_new
                ON hr_old.parent = hr_new.parent
                AND hr_old.parenttype = hr_new.parenttype
            WHERE hr_old.role = %s AND hr_new.role = %s
        """, (old_name, new_name))

    # Update remaining references
    frappe.db.sql(
        "UPDATE `{table}` SET `{field}` = %s WHERE `{field}` = %s".format(
            table=table_name,
            field=fieldname
        ),
        (new_name, old_name)
    )


def _delete_role(old_name):
    """
    Delete the old role record after all references have been migrated.
    """
    if frappe.db.exists("Role", old_name):
        frappe.delete_doc("Role", old_name, force=True, ignore_permissions=True)
