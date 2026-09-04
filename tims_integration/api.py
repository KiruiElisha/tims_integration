import frappe
from frappe import _
from frappe.utils import cint, flt


def sales_invoice_validate(doc, method=None):
    """
    Catch at validate time what would otherwise surface as a device rejection or,
    worse, as a silently wrong credit note.
    """
    tims_settings = frappe.get_single('TIMS Device Setup')
    if not tims_settings.send_invoices_to_kra_on_submit:
        return

    if not doc.get("is_return") or not tims_settings.send_credit_notes:
        return

    from tims_integration.services.rest import ADJUSTMENT_PRICE, original_invoice_of

    original = original_invoice_of(doc)
    if not original:
        frappe.throw(_("Set 'TIMS Original Invoice' (or 'Return Against') so TIMS "
                       "can be told which invoice this credit note adjusts."))

    if doc.get("custom_tims_adjustment_type") == ADJUSTMENT_PRICE:
        validate_price_adjustment(doc, original)


def validate_price_adjustment(doc, original):
    """
    A price adjustment moves money, not goods.

    update_stock must be off or ERPNext brings the goods back into stock and
    credits COGS for a return that never happened. return_against must be blank
    or ERPNext charges this against the original invoice's return quantity and
    refuses the next adjustment - which is the whole problem this type exists to
    solve, since prices keep moving and one invoice needs adjusting repeatedly.
    """
    if doc.get("update_stock"):
        frappe.throw(_("A Price Adjustment must have 'Update Stock' off: no goods "
                       "are returned, so stock and COGS must not move."))

    if doc.return_against:
        frappe.throw(_("A Price Adjustment must leave 'Return Against' blank and use "
                       "'TIMS Original Invoice' instead. ERPNext counts a return "
                       "against the original invoice's quantity, which would block "
                       "every later adjustment."))

    from tims_integration.services.allowance import remaining, summary

    headroom = summary(original)
    if not headroom:
        frappe.throw(_("{0} has no accepted TIMS sale recorded, so nothing can be "
                       "adjusted against it yet.").format(original))

    requested = abs(flt(doc.base_grand_total))
    available = float(headroom["remaining_amount"])
    if requested > available + 0.01:
        frappe.throw(
            _("This adjustment is {0} but {1} has only {2} left to credit "
              "({3} of {4} already credited).").format(
                requested, original, round(available, 2),
                round(float(headroom["credited_amount"]), 2),
                round(float(headroom["original_amount"]), 2)),
            title=_("Exceeds TIMS Credit Allowance"))

    # Quantity is its own budget on the TIMS device (E219/E220): a line can
    # decline more than is left even when the money total above still fits.
    per_line = remaining(original)
    for item in doc.items:
        entry = per_line.get(item.item_name)
        qty = abs(flt(item.qty))
        if not entry:
            frappe.throw(_("{0} has no accepted TIMS sale recorded for '{1}', so nothing can be "
                           "adjusted against it.").format(original, item.item_name))
        if qty > float(entry["remaining_qty"]) + 0.01:
            frappe.throw(
                _("This adjustment declares {0} of {1} but only {2} is left to credit "
                  "against {3} ({4} of {5} already credited).").format(
                    qty, item.item_name, round(float(entry["remaining_qty"]), 2), original,
                    round(float(entry["credited_qty"]), 2), round(float(entry["original_qty"]), 2)),
                title=_("Exceeds TIMS Quantity Allowance"))

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
        # A caller that already showed the user a preview and got explicit
        # confirmation (the Price Adjustment modal) sets this flag before
        # calling submit(), so this attempt sends immediately instead of
        # gating on concerns the user has already seen and accepted.
        send_request(doc.name, doc=doc, confirmed=cint(doc.flags.get("tims_confirmed_send")))

    except Exception as e:
        frappe.log_error(
            title="Failed to send invoice to TIMS",
            message=frappe.get_traceback()
        )
        if not tims_settings.allow_submission_on_failure:
            frappe.throw(
                _("Failed to send invoice to TIMS: {0}").format(str(e))
            ) 
