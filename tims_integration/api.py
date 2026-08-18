import frappe
from frappe import _

def sales_invoice_on_submit(doc, method):
    """Handle TIMS submission on Sales Invoice submit"""

    from tims_integration.services.rest import send_request, skip

    tims_settings = frappe.get_single('TIMS Device Setup')

    # Each of these used to return silently, which left no trace anywhere that an
    # invoice had been deliberately skipped - indistinguishable from a broken send.
    if not tims_settings.send_invoices_to_kra_on_submit:
        skip(doc.name, "'Send Invoices To KRA On Submit' is off in TIMS Device Setup.")
        return

    if doc.is_return and not tims_settings.send_credit_notes:
        skip(doc.name, "'Send Credit Notes To KRA' is off in TIMS Device Setup.")
        return

    if doc.custom_sent_to_kra:
        skip(doc.name, "Already sent to KRA.")
        return

    try:
        send_request(doc.name, doc=doc)
        
    except Exception as e:
        frappe.log_error(
            title="Failed to send invoice to TIMS",
            message=frappe.get_traceback()
        )
        if not tims_settings.allow_submission_on_failure:
            frappe.throw(
                _("Failed to send invoice to TIMS: {0}").format(str(e))
            ) 
