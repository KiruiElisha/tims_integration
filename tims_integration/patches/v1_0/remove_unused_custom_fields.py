import frappe

# Left over from an earlier eTIMS/VSCU-shaped schema. The app never reads or writes
# any of them, and they were verified empty before removal. Fixtures only ever create
# or update, so sites that already have these fields need them deleted explicitly.
UNUSED_FIELDS = [
    "custom_tax_exemption_id",
    "custom_fiscal_invoice_number",
    "custom_fiscal_verification_url",
    "custom_is_fiscalized",
    "custom_qr_image",
    "custom_submission_sequence_number",
    "custom_internal_data",
    "custom_receipt_signature",
    "custom_successfully_submitted",
    "custom_scu_id",
    "custom_qr_code",
    "custom_control_unit_date_time",
    "custom_total_receipt_number",
    "custom_current_receipt_number",
    "custom_transaction_progress_code",
    "custom_payment_type_code",
    # layout breaks that only existed to hold the fields above
    "custom_column_break_xlgtx",
    "custom_column_break_194d5",
    "custom_section_break_u5cdx",
    "custom_column_break_urllr",
    "custom_column_break_arjmp",
]


def execute():
    remove_field_order_property_setter()

    columns = set(frappe.db.get_table_columns("Sales Invoice"))

    for fieldname in UNUSED_FIELDS:
        name = "Sales Invoice-{0}".format(fieldname)
        if not frappe.db.exists("Custom Field", name):
            continue

        # Never drop a field somebody has populated, even though these were empty
        # everywhere we checked - a site we have not seen may have used one.
        if fieldname in columns:
            used = frappe.db.sql(
                "select 1 from `tabSales Invoice` where `{0}` is not null "
                "and `{0}` != '' limit 1".format(fieldname)
            )
            if used:
                frappe.log_error(
                    title="TIMS KRA: unused field kept",
                    message="{0} still holds data, so it was not removed.".format(fieldname)
                )
                continue

        frappe.delete_doc("Custom Field", name, ignore_permissions=True, force=True)

    frappe.clear_cache(doctype="Sales Invoice")


def remove_field_order_property_setter():
    """
    A field_order Property Setter froze the whole Sales Invoice layout as a 257-entry
    list. It pinned the TIMS fields wherever they happened to sit when it was
    exported, overriding every insert_after, and it names fields this patch deletes.
    Freezing a core ERPNext doctype's field order this way also hides any field
    ERPNext adds later, so it is removed rather than regenerated.
    """
    name = "Sales Invoice-main-field_order"
    if frappe.db.exists("Property Setter", name):
        frappe.delete_doc("Property Setter", name, ignore_permissions=True, force=True)
