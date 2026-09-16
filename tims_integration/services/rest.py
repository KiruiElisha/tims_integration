import frappe
import json
import requests
from frappe import _
from frappe.utils import cint, flt, getdate
from datetime import datetime

@frappe.whitelist()
def send_request(invoice, doc=None, confirmed=0):
    try:
        device_setup = frappe.get_single('TIMS Device Setup')
        # Reuse the caller's document when there is one. Re-fetching during the
        # on_submit hook yields a second in-memory copy, so the fiscal fields get
        # written to a document the submit flow is not holding.
        # send_request is whitelisted, so anything arriving over HTTP as `doc` is a
        # string, not a Document - only trust an actual Document instance.
        if not hasattr(doc, "doctype"):
            doc = frappe.get_doc("Sales Invoice", invoice)

        if device_setup.status != 'Active':
            skip(invoice, "TIMS Device Setup status is {0}, not Active.".format(
                device_setup.status))
            return

        if not is_valid_posting_date(doc, device_setup):
            skip(invoice, "Posting date {0} is not today and 'Allow Other Day "
                          "Posting' is off.".format(doc.posting_date))
            return

        payload, unclassified = build_payload(doc, device_setup)

        # Fiscalisation cannot be undone, so anything that would declare figures
        # differing from the invoice is never sent on the user's behalf. The invoice
        # is left unsent until somebody reviews it and confirms via 'Send to TIMS'.
        concerns = get_submission_concerns(doc, payload, unclassified)
        if concerns and not cint(confirmed):
            skip(invoice, " ".join(concerns) +
                 " Review the invoice and use 'Send to TIMS' to confirm the amounts "
                 "before they are declared to KRA.")
            return

        send_payload(payload, invoice, doc)
    except Exception as e:
        handle_exception(e)


def skip(invoice, reason):
    """
    Nothing was sent to KRA. Log it: a msgprint is invisible when the submit runs
    from the on_submit hook, a background job or the API, which makes a skipped
    submission indistinguishable from a broken one.
    """
    frappe.log_error(
        title="TIMS KRA: invoice not sent",
        message="{0} was not sent to KRA.\n\nReason: {1}".format(invoice, reason)
    )
    frappe.msgprint(
        msg="{0} was not sent to KRA. {1}".format(invoice, reason),
        title="TIMS Submission Skipped",
        indicator='orange',
    )


TOTAL_TOLERANCE = 0.01


def get_submission_concerns(doc, payload, unclassified):
    """
    Reasons a human should look at this invoice before it is declared to KRA.
    Empty means the payload agrees with the invoice and every band was resolved
    from real tax data, so it can be sent automatically on submit.
    """
    concerns = []

    if unclassified:
        items = ", ".join(
            "{0} (qty {1}, net {2})".format(u["item_code"], u["qty"], u["net_amount"])
            for u in unclassified
        )
        concerns.append(
            "No tax template or invoice tax row could be resolved for: {0}; "
            "{1}% VAT would be assumed.".format(items, ASSUMED_RATE))

    # The payload's VAT comes from item tax templates, while the invoice total comes
    # from its Sales Taxes and Charges rows. They can disagree - an item tax template
    # with no matching tax row gives KRA a higher total than the customer was billed.
    grand_total = round(float(doc.get("base_grand_total") or 0), 2)
    declared = round(float(payload["total"]), 2)
    if abs(grand_total - declared) >= TOTAL_TOLERANCE:
        concerns.append(
            "Invoice grand total is {0} but {1} would be declared to KRA.".format(
                grand_total, declared))

    return concerns


@frappe.whitelist()
def preview_submission(invoice):
    """
    Builds the payload without sending it, so the user can be shown exactly what
    would be declared to KRA and confirm it. Returns the payload, the items whose
    tax band had to be assumed, and the invoice totals to check the payload against.
    """
    device_setup = frappe.get_single('TIMS Device Setup')
    doc = frappe.get_doc("Sales Invoice", invoice)
    payload, unclassified = build_payload(doc, device_setup)

    concerns = get_submission_concerns(doc, payload, unclassified)

    return {
        "invoice": doc.name,
        "payload": payload,
        "unclassified": unclassified,
        "concerns": concerns,
        "assumed_rate": ASSUMED_RATE,
        "already_sent": cint(doc.custom_sent_to_kra),
        "invoice_totals": {
            "net_total": float(doc.base_net_total or 0),
            "grand_total": float(doc.base_grand_total or 0),
            "currency": doc.currency,
        },
        "totals_match": not any(c.startswith("Invoice grand total") for c in concerns),
    }


def is_valid_posting_date(doc, device_setup):
    if device_setup.allow_other_day_posting:
        return True

    return getdate(doc.posting_date) == getdate()


def build_payload(doc, device_setup):
    """
    Returns (payload, unclassified). `unclassified` lists items whose tax band could
    not be resolved and for which the standard 16% VAT band was assumed. The caller
    decides what to do about it - the assumption is never applied silently, because
    it changes the total reported to KRA away from the invoice total.
    """
    payment_method = "Cash" if doc.status == 'Paid' else 'Credit'
    # Blank unless a till is explicitly configured: the device prefixes a set till
    # (e.g. "01") to the invoice number on the fiscal receipt.
    till_no = str(device_setup.till_number or "").strip()
    rct_no = get_rct_no(doc)
    customer_pin = get_customer_pin(doc)
    invoice_items = get_invoice_items(doc.name)
    default_tax = get_default_invoice_tax(doc.name)

    vat_values = initialize_vat_values()
    items = []
    unclassified = []

    adjustment = is_price_adjustment(doc)

    for item in invoice_items:
        tax_title = item.tax_title
        tax_rate = item.tax_rate
        if not tax_title:
            tax_title, tax_rate = default_tax

        new_item, taxable_amount, tax_amount, category, assumed = calculate_tax(
            item, tax_title, tax_rate)

        if adjustment:
            new_item = encode_declared_adjustment_line(new_item, item)

        vat_values = update_vat_values(vat_values, category, taxable_amount, tax_amount)
        items.append(new_item)

        if assumed:
            unclassified.append({
                "item_code": item.item_code,
                "item_name": item.item_name,
                "qty": float(item.qty or 0),
                "net_amount": float(item.base_net_amount or 0),
            })

    payload = create_payload(doc, vat_values, items, payment_method, customer_pin, till_no, rct_no)
    return payload, unclassified


RCT_NO_MAX_LENGTH = 18


def get_rct_no(doc):
    """
    The receipt number reported to KRA. The device rejects anything longer than 18
    characters ("Invoice/Receipt # cannot be more than 18 Characters"), and the
    default ERPNext series already exceeds that: ACC-SINV-2026-00554 is 19.

    Separators are dropped first, since that keeps the whole number intact and stays
    unique. Only if that is still too long do we keep the trailing characters, which
    carry the counter.
    """
    name = doc.name
    if len(name) <= RCT_NO_MAX_LENGTH:
        return name

    compact = "".join(c for c in name if c.isalnum())
    rct_no = compact if len(compact) <= RCT_NO_MAX_LENGTH else compact[-RCT_NO_MAX_LENGTH:]

    frappe.log_error(
        title="TIMS KRA: receipt number shortened",
        message="{0} is {1} characters, over the {2} the device accepts; "
                "reported to KRA as {3}.".format(
                    name, len(name), RCT_NO_MAX_LENGTH, rct_no)
    )

    return rct_no


def get_customer_pin(doc):
    """
    KRA PIN for the buyer. Sales Invoice.tax_id is the authoritative value (it is
    fetched from the Customer at invoice time and can be overridden per invoice);
    fall back to the Customer record for invoices where it was never populated.
    """
    pin = (doc.get("tax_id") or "").strip()
    if pin:
        return pin

    return (frappe.db.get_value("Customer", doc.customer, "tax_id") or "").strip()


# Item columns this app adds itself, via the custom fields install.setup() creates.
# They only carry Price Adjustment figures, so every other invoice is complete
# without them - see get_optional_item_columns for why they are not assumed.
OPTIONAL_ITEM_COLUMNS = ("custom_tims_unit_price", "custom_tims_discount")


def get_optional_item_columns():
    """
    The app's own item columns, but only the ones the site actually has.

    Code reaches a site before its migration does: a deploy that ships a new
    custom field leaves a window where the column is not there yet, and naming it
    in the SELECT fails the whole query. That would stop every submission on the
    site, not just the Price Adjustments these two columns are for, so a missing
    column is treated as a missing value instead - which is exactly what
    encode_declared_adjustment_line already falls back on.
    """
    present = frappe.db.get_table_columns("Sales Invoice Item")
    missing = [c for c in OPTIONAL_ITEM_COLUMNS if c not in present]

    if missing:
        frappe.log_error(
            title="TIMS KRA: item fields missing",
            message="Sales Invoice Item is missing {0}. Price Adjustments will be declared "
                    "from the ERPNext figures rather than the declared ones until this site "
                    "is migrated (bench --site <site> migrate).".format(", ".join(missing))
        )

    return [c for c in OPTIONAL_ITEM_COLUMNS if c in present]


def get_invoice_items(invoice):
    optional = "".join("sii.{0}, ".format(c) for c in get_optional_item_columns())
    query = """
        SELECT sii.name, sii.item_code, sii.item_name, sii.rate, sii.base_rate, sii.base_amount,
        sii.base_net_rate, sii.base_net_amount, sii.qty, sii.item_tax_template, {optional}
        it_template.title AS tax_title, it_template_detail.tax_rate AS tax_rate
        FROM `tabSales Invoice Item` sii
        LEFT JOIN `tabItem Tax Template` it_template ON it_template.name = sii.item_tax_template
        LEFT JOIN `tabItem Tax Template Detail` it_template_detail ON it_template_detail.parent = sii.item_tax_template
        WHERE sii.parent = %s
    """.format(optional=optional)
    return frappe.db.sql(query, invoice, as_dict=True)


def get_default_invoice_tax(invoice):
    """
    Fallback tax type/rate for items with no item-level Item Tax Template override,
    sourced from the invoice's own Sales Taxes and Charges row (the common case where
    a single VAT template is applied to the whole invoice rather than per item).
    """
    rows = frappe.db.get_all(
        "Sales Taxes and Charges",
        filters={"parenttype": "Sales Invoice", "parent": invoice},
        fields=["description", "account_head", "rate"],
        order_by="idx asc",
        limit=1,
    )
    if not rows:
        return None, 0

    row = rows[0]
    title = (row.description or row.account_head or "").strip()
    return title, row.rate or 0


def initialize_vat_values():
    return {
        "VAT_A_NET": 0,
        "VAT_A": 0,
        "VAT_B_NET": 0,
        "VAT_B": 0,
        "VAT_C_NET": 0,
        "VAT_C": 0,
        "VAT_D_NET": 0,
        "VAT_D": 0,
        "VAT_E_NET": 0,
        "VAT_E": 0,
        "VAT_F_NET": 0,
        "VAT_F": 0,
    }


def classify_tax(tax_title, tax_rate):
    """
    Maps a resolved tax template title / rate to a KRA VAT band.
    Returns (category, rate) where category is one of:
    "16", "8", "10", "2", "zero", "exempt", or None if nothing could be resolved.
    """
    rate = float(tax_rate or 0)
    label = (tax_title or "").strip().lower()

    if "exempt" in label:
        return "exempt", 0.0
    if "zero" in label:
        return "zero", 0.0
    if rate:
        return str(int(round(rate))), rate
    return None, 0.0


# taxtype value the TIMS API expects per band. Rated bands send their rate as-is
# ("16", "8", ...); the two non-VATable bands have their own literals.
TAX_TYPE_CODES = {
    "zero": "0",
    "exempt": "exempted",
}


# Band assumed for an item whose tax cannot be resolved. It is only ever applied
# after the user has confirmed it: see build_payload and UNCLASSIFIED_REASON.
ASSUMED_CATEGORY, ASSUMED_RATE = "16", 16.0


def calculate_tax(item, tax_title, tax_rate):
    category, rate = classify_tax(tax_title, tax_rate)
    assumed = category is None
    if assumed:
        category, rate = ASSUMED_CATEGORY, ASSUMED_RATE

    tax_value = 1 + (rate / 100)

    qty = float(item.qty or 1.0)

    # base_net_rate is always VAT-exclusive, whether or not the invoice tax is
    # included_in_print_rate. KRA expects unitPrice inclusive of VAT.
    base_net_rate = float(item.base_net_rate or 0)

    unit_price = round(base_net_rate * tax_value, 2)
    discount = 0.0

    product_code = get_hs_code(item.item_code, category)

    new_item = {
        "productCode": product_code,
        "productDesc": item.item_name,
        "quantity": abs(float(qty)),
        "unitPrice": abs(float(unit_price)),
        "discount": abs(float(discount)),
        "taxtype": TAX_TYPE_CODES.get(category, category),
    }

    taxable_amount = base_net_rate * qty - discount
    tax_amount = taxable_amount * (rate / 100)

    return new_item, taxable_amount, tax_amount, category, assumed


ADJUSTMENT_PRICE = "Price Adjustment"


def is_price_adjustment(doc):
    """A credit note that moves money without moving goods."""
    return bool(doc.get("is_return")) and \
        doc.get("custom_tims_adjustment_type") == ADJUSTMENT_PRICE


def encode_declared_adjustment_line(new_item, item):
    """
    Declare one Price Adjustment line exactly as it appears on this document:
    the row's own qty (the real quantity involved), custom_tims_unit_price
    (the original invoice's declared unit price) and custom_tims_discount (the
    total discount), all set once by create_price_adjustment and copied here
    unchanged. Nothing is looked up or recalculated at send time, so the
    document and the payload can never diverge - what an auditor sees on the
    row is exactly what was declared to TIMS.

    A row with no TIMS unit price (a hand-built return, or one predating this
    field) is left exactly as ERPNext built it: the figures are still correct,
    only unoptimised, and refusing to send would be worse than sending a line
    the device accepts anyway.
    """
    unit_price = flt(item.get("custom_tims_unit_price"))
    if unit_price <= 0:
        return new_item

    new_item = dict(new_item)
    new_item["unitPrice"] = float(unit_price)
    new_item["quantity"] = float(abs(flt(item.get("qty"))))
    new_item["discount"] = float(abs(flt(item.get("custom_tims_discount"))))
    return new_item


@frappe.whitelist()
def create_price_adjustment(original_invoice, items):
    """
    Build a *draft* Price Adjustment credit note against ``original_invoice``
    from a set of per-item quantities and discounts, so the "TIMS" button on the
    invoice can offer this without the user hand-building a return.

    Deliberately left unsubmitted: submitting fires sales_invoice_on_submit,
    which sends to KRA immediately when 'Send Invoices To KRA On Submit' is on -
    before the caller has had any chance to preview what would be declared. The
    caller previews this draft, then calls submit_and_send_price_adjustment to
    commit and send it in one step once the user has confirmed the figures.

    ``items`` is a list of ``{"item_code": ..., "qty": ..., "discount": ...}``:
    the real quantity and total discount the user is declaring for that item,
    at the unit price the original invoice actually declared to TIMS - not a
    target money value for the system to reverse-engineer a quantity from.

    The row is built so nothing needs recalculating between what the document
    shows and what gets sent: 'Qty' is the real quantity (not a placeholder),
    'TIMS Unit Price' is the original invoice's own declared price, and 'TIMS
    Discount' is the discount, both stored verbatim for build_payload to copy
    into the payload unchanged. 'Rate' is the one derived figure - the net,
    post-discount price - because ERPNext always computes this row's amount as
    qty x rate and has no other way to net out a discount, so this is what
    keeps the credited money correct without touching the other three.

    All the guardrails - update_stock off, return_against blank, headroom not
    exceeded - are the ones sales_invoice_validate already enforces on any
    Price Adjustment; this function does not duplicate them, it just builds a
    document that goes through the same validate hook.
    """
    if isinstance(items, str):
        items = json.loads(items)

    items = [it for it in items if flt(it.get("qty")) > 0]
    if not items:
        frappe.throw(_("Enter a quantity to credit for at least one item."))

    from tims_integration.services.allowance import remaining

    headroom = remaining(original_invoice)

    original = frappe.get_doc("Sales Invoice", original_invoice)
    original_rows = {row.item_code: row for row in original.items}

    doc = frappe.new_doc("Sales Invoice")
    doc.customer = original.customer
    doc.company = original.company
    doc.currency = original.currency
    doc.selling_price_list = original.selling_price_list
    doc.is_return = 1
    doc.update_stock = 0
    doc.custom_tims_original_invoice = original.name
    doc.custom_tims_adjustment_type = ADJUSTMENT_PRICE

    for it in items:
        row = original_rows.get(it.get("item_code"))
        if not row:
            frappe.throw(_("{0} is not an item on {1}.").format(it.get("item_code"), original.name))

        entry = headroom.get(row.item_name)
        if not entry or flt(entry["unit_price"]) <= 0:
            frappe.throw(_("{0} has no accepted TIMS sale recorded for '{1}', so nothing can be "
                           "declared for it yet.").format(original.name, row.item_name))

        qty = flt(it["qty"])
        discount = flt(it.get("discount"))
        unit_price = flt(entry["unit_price"])
        net_rate = unit_price - (discount / qty if qty else 0)
        if net_rate <= 0:
            frappe.throw(_("The discount for {0} cannot be {1} or more of {2} x {3}.").format(
                row.item_name, discount, qty, unit_price))

        doc.append("items", {
            "item_code": row.item_code,
            "item_name": row.item_name,
            "description": row.description,
            "uom": row.uom,
            "stock_uom": row.stock_uom,
            "conversion_factor": row.conversion_factor or 1,
            "qty": -qty,
            "rate": net_rate,
            "income_account": row.income_account,
            "cost_center": row.cost_center,
            "item_tax_template": row.item_tax_template,
            "warehouse": row.warehouse,
            "custom_tims_unit_price": unit_price,
            "custom_tims_discount": discount,
        })

    for tax in original.taxes:
        doc.append("taxes", {
            "charge_type": tax.charge_type,
            "account_head": tax.account_head,
            "description": tax.description,
            "rate": tax.rate,
            "cost_center": tax.cost_center,
            "included_in_print_rate": tax.included_in_print_rate,
        })

    doc.insert()
    return doc.name


@frappe.whitelist()
def submit_and_send_price_adjustment(invoice):
    """
    Commits a draft built by create_price_adjustment and makes sure it reaches
    TIMS, once the user has previewed it and confirmed the figures.

    Submitting fires sales_invoice_on_submit, which makes its own send attempt.
    The tims_confirmed_send flag tells that attempt the user already saw and
    accepted this exact payload, so it sends immediately instead of skipping on
    concerns it has no way to know were already shown. Without the flag, a
    second explicit send here after a skip would either be redundant (if
    on_submit already sent it - it would just report "Already sent to KRA",
    which reads as a failure right after a success) or duplicate the concerns
    message the user already dismissed.

    'Send Invoices To KRA On Submit' being off is the one thing the flag can't
    route around - on_submit returns before even looking at it - so an explicit
    send is still needed for that case, gated on custom_sent_to_kra so it never
    fires when on_submit already succeeded.
    """
    doc = frappe.get_doc("Sales Invoice", invoice)
    if doc.docstatus == 0:
        doc.flags.tims_confirmed_send = 1
        doc.submit()
    if not doc.custom_sent_to_kra:
        send_request(doc.name, doc=doc, confirmed=1)
    return doc.name


# KRA expects a fixed HS code as the productCode for non-VATable sales, regardless
# of the item's own customs tariff number.
BAND_HS_CODES = {
    "exempt": "0043.11.00",
    "zero": "0022.12.00",
}


def get_hs_code(item_code, category):
    """
    Returns the productCode to report for an item: the static KRA HS code for
    exempt/zero-rated sales, or the plain item code for VATable sales.
    """
    return BAND_HS_CODES.get(category, item_code)


VAT_BUCKETS = {
    "16": "A",
    "8": "B",
    "10": "C",
    "2": "D",
    "zero": "E",
    "exempt": "F",
}


def update_vat_values(vat_values, category, taxable_amount, tax_amount):
    bucket = VAT_BUCKETS.get(category, "A")
    vat_values["VAT_{0}_NET".format(bucket)] += taxable_amount
    vat_values["VAT_{0}".format(bucket)] += tax_amount

    return vat_values


def original_invoice_of(doc):
    """
    The invoice a credit note adjusts.

    custom_tims_original_invoice is authoritative and is the only field set on a
    price adjustment. Such a credit note leaves return_against blank on purpose:
    ERPNext counts a return against the original's quantity, so using it would
    make the first price adjustment the last one possible.
    """
    return doc.get("custom_tims_original_invoice") or doc.get("return_against")


def get_original_cuin(doc):
    """
    A refund must quote the CUIN of the invoice it adjusts, which lives on the
    original invoice's KRA Response - not on the credit note itself.
    """
    original = original_invoice_of(doc)
    if not original:
        frappe.throw("Credit note {0} does not say which invoice it adjusts. Set "
                     "'TIMS Original Invoice' (or 'Return Against' for a goods "
                     "return).".format(doc.name))

    cuin = frappe.db.get_value("Sales Invoice", original, "custom_cuin")
    if not cuin:
        cuin = frappe.db.get_value(
            "KRA Response",
            {"invoice_number": original, "response_code": "000"},
            "cuin",
            order_by="creation desc",
        )

    if not cuin:
        frappe.throw("Invoice {0} has no KRA CUIN recorded, so a refund for it "
                     "cannot be sent to TIMS.".format(original))

    return cuin


def create_payload(doc, vat_values, items, payment_method, customer_pin, till_no, rct_no):
    total = sum([
        vat_values["VAT_A_NET"] + vat_values["VAT_A"],
        vat_values["VAT_B_NET"] + vat_values["VAT_B"],
        vat_values["VAT_C_NET"] + vat_values["VAT_C"],
        vat_values["VAT_D_NET"] + vat_values["VAT_D"],
        vat_values["VAT_E_NET"],
        vat_values["VAT_F_NET"]
    ])

    payload_type = "sales" if not doc.is_return else "refund"
    cuin = get_original_cuin(doc) if doc.is_return else ""

    # A refund is identified by saleType, not by sign: the device rejects the payload
    # with a totals error if the amounts come through negative.
    def amount(value):
        return round(abs(float(value)), 2)

    payload = {
        "saleType": payload_type,
        "cuin": cuin,
        "till": till_no,
        "rctNo": rct_no,
        "total": amount(total),
        "Paid": amount(total),
        "Payment": payment_method,
        "CustomerPIN": customer_pin,
        "VAT_A_Net": amount(vat_values["VAT_A_NET"]),
        "VAT_A": amount(vat_values["VAT_A"]),
        "VAT_B_Net": amount(vat_values["VAT_B_NET"]),
        "VAT_B": amount(vat_values["VAT_B"]),
        "VAT_C_Net": amount(vat_values["VAT_C_NET"]),
        "VAT_C": amount(vat_values["VAT_C"]),
        "VAT_D_Net": amount(vat_values["VAT_D_NET"]),
        "VAT_D": amount(vat_values["VAT_D"]),
        "VAT_E_Net": amount(vat_values["VAT_E_NET"]),
        "VAT_E": amount(vat_values["VAT_E"]),
        "VAT_F_Net": amount(vat_values["VAT_F_NET"]),
        "VAT_F": amount(vat_values["VAT_F"]),
        "data": items
    }

    return payload


def send_payload(payload, invoice, doc):
    device_setup = frappe.get_single('TIMS Device Setup')
    url = f"http://{device_setup.ip}:{device_setup.port}/api/values/PostTims"

    try:
        response = requests.post(url, json=payload, timeout=60)
    except Exception:
        # Nothing reached the device, so there is no response to record - log the
        # real cause rather than reporting every failure as a timeout.
        frappe.log_error(
            title="TIMS KRA: device unreachable",
            message="POST {0} for {1} failed.\n\nPayload:\n{2}\n\n{3}".format(
                url, invoice, payload, frappe.get_traceback())
        )
        frappe.msgprint(
            msg="Could not reach the TIMS/ETR Machine at {0}. Please make sure it is "
                "active - see the Error Log for details.".format(url),
            title="Error Message",
            indicator='red',
        )
        record_kra_response(
            {"ResponseCode": "", "Message": "Device unreachable at {0}".format(url)},
            invoice,
            payload,
        )
        return

    handle_response(response, invoice, doc, payload)


SIGNING_TIME_FORMATS = (
    # The device returns minute precision: "2026-08-18 14:04".
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%d/%m/%Y %H:%M:%S",
    "%d-%m-%Y %H:%M:%S",
    "%Y%m%d%H%M%S",
    "%d/%m/%Y",
    "%Y-%m-%d",
)


def parse_signing_time(value):
    """
    The device returns dtStmp as a plain string whose format varies by firmware, but
    KRA Response.signing_time is a Datetime. Return None rather than let an
    unparseable stamp abort the insert and lose the whole response record.
    """
    if not value:
        return None

    value = str(value).strip()
    for fmt in SIGNING_TIME_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue

    frappe.log_error(
        title="TIMS KRA: unparsed signing time",
        message="Could not parse dtStmp {0!r} in any known format.".format(value)
    )
    return None


def parse_response(response):
    """
    TIMS returns JSON on success but can return an HTML error page or an empty body
    when it rejects a payload. Always yield a dict so the exchange is still recorded.
    """
    try:
        data = json.loads(response.text)
    except ValueError:
        return {
            "ResponseCode": str(response.status_code),
            "Message": (response.text or "").strip()[:1000] or "Empty response from TIMS device",
        }

    if not isinstance(data, dict):
        return {"ResponseCode": str(response.status_code), "Message": str(data)[:1000]}

    return data


KRA_RESPONSE_SAVEPOINT = "tims_kra_response"


def record_kra_response(data, invoice, payload):
    """
    Persists the exchange inside a savepoint. This runs from the Sales Invoice
    on_submit hook, so it must never call frappe.db.commit() (which would commit a
    half-submitted invoice) or a bare frappe.db.rollback() (which would discard the
    in-flight submission entirely). Rolling back to a savepoint undoes only a failed
    insert, leaving the surrounding submit intact.
    """
    frappe.db.savepoint(KRA_RESPONSE_SAVEPOINT)
    try:
        kra_response = frappe.get_doc({
            "doctype": "KRA Response",
            "response_code": str(data.get("ResponseCode") or ''),
            "message": str(data.get("Message") or ''),
            "tin": str(data.get("TSIN") or ''),
            "cusn": str(data.get("CUSN") or ''),
            "cuin": str(data.get("CUIN") or ''),
            "qr_code": str(data.get("QRCode") or ''),
            "signing_time": parse_signing_time(data.get("dtStmp")),
            "invoice_number": invoice,
            # JSON, not repr: the allowance tracker reads these payloads back to
            # work out what an invoice has left to be credited. Older rows are
            # repr and are still parsed, see services.allowance.parse_payload.
            "payload_sent": json.dumps(payload, indent=2, default=str)
        })
        kra_response.insert(ignore_permissions=True)
        return kra_response.name
    except Exception:
        frappe.db.rollback(save_point=KRA_RESPONSE_SAVEPOINT)
        frappe.log_error(
            title="TIMS KRA: could not record response",
            message="Invoice: {0}\n\nDevice reply:\n{1}\n\nPayload:\n{2}\n\n{3}".format(
                invoice, data, payload, frappe.get_traceback())
        )
        return None


def handle_response(response, invoice, doc, payload):
    data = parse_response(response)
    record_kra_response(data, invoice, payload)

    if data.get('ResponseCode') == '000':
        update_doc_with_response(doc, data, payload)
    else:
        frappe.msgprint(
            msg="Invoice Submission to KRA Failed. Please Check KRA Response Generated.",
            title='Error Message',
            indicator='red',
        )


# Each VAT band's net/tax pair is mirrored onto the invoice. Band F (exempt) has no
# pair of custom fields, so only A-E are stored.
RECORDED_VAT_BANDS = ("A", "B", "C", "D", "E")


def get_vat_band_values(payload):
    """
    The per-band totals and the set of tax types actually reported, taken from the
    payload that was sent so the invoice records exactly what KRA received.
    """
    values = {}
    for band in RECORDED_VAT_BANDS:
        values["custom_taxbl_amount_{0}".format(band.lower())] = payload.get("VAT_{0}_Net".format(band))
        values["custom_tax_{0}".format(band.lower())] = payload.get("VAT_{0}".format(band))

    tax_types = []
    for item in payload.get("data") or []:
        tax_type = str(item.get("taxtype") or "").strip()
        if tax_type and tax_type not in tax_types:
            tax_types.append(tax_type)

    values["custom_taxation_type"] = ", ".join(tax_types)

    return values


def update_doc_with_response(doc, data, payload=None):
    signing_time = parse_signing_time(data.get("dtStmp"))

    values = {
        "custom_tims_response_code": data.get("ResponseCode"),
        "custom_tsin": data.get("TSIN"),
        "custom_cusn": data.get("CUSN"),
        "custom_cuin": data.get("CUIN"),
        "custom_kra_qr_code_data": data.get("QRCode"),
        # custom_kra_signing_time is a Date field, so store the date part only.
        "custom_kra_signing_time": signing_time.date() if signing_time else None,
        "custom_sent_to_kra": 1,
    }

    if payload:
        values.update(get_vat_band_values(payload))

    # db_set writes straight to the row. doc.save() cannot be used here: this runs
    # from the Sales Invoice on_submit hook, where saving collides with the in-flight
    # submit and aborts before the fiscal details are stored.
    for field, value in values.items():
        doc.db_set(field, value, update_modified=False)


def handle_exception(exception):
    frappe.log_error(
        title="TIMS KRA Error",
        message="{0}\n\n{1}".format(exception, frappe.get_traceback())
    )
    frappe.msgprint(
        msg="Something Wrong, Please try again or check the "+"<a style='color: red; font-weight: bold;' href='/app/error-log'>Error Logs</a>",
        title="Error Message",
        indicator='red',
    )
    return exception


@frappe.whitelist()
def diagnose(invoice):
    """
    Runs the whole submission path for an invoice and returns every intermediate
    result rather than routing failures to msgprint/Error Log. Use this when nothing
    appears in the KRA Response list and it is unclear how far the request got:

        bench --site <site> execute \
            tims_integration.services.rest.diagnose --args "['SIN00005']"
    """
    report = {"invoice": invoice, "stage": "start"}

    try:
        device_setup = frappe.get_single('TIMS Device Setup')
        doc = frappe.get_doc("Sales Invoice", invoice)

        report["device_status"] = device_setup.status
        report["device_url"] = "http://{0}:{1}/api/values/PostTims".format(
            device_setup.ip, device_setup.port)
        report["send_on_submit"] = device_setup.send_invoices_to_kra_on_submit
        report["send_credit_notes"] = device_setup.send_credit_notes
        report["till_number"] = device_setup.till_number
        report["is_return"] = doc.is_return
        report["already_sent"] = doc.custom_sent_to_kra
        report["posting_date_ok"] = is_valid_posting_date(doc, device_setup)

        report["stage"] = "build_payload"
        report["payload"], report["unclassified"] = build_payload(doc, device_setup)

        report["stage"] = "post"
        response = requests.post(report["device_url"], json=report["payload"], timeout=60)
        report["http_status"] = response.status_code
        report["raw_body"] = (response.text or "")[:2000]

        report["stage"] = "parse"
        data = parse_response(response)
        report["parsed"] = data

        report["stage"] = "record"
        report["kra_response"] = record_kra_response(data, invoice, report["payload"])

        report["stage"] = "done"
    except Exception as e:
        report["error"] = str(e)
        report["traceback"] = frappe.get_traceback()

    return report


@frappe.whitelist()
def check_setup():
    """
    Reports whether the app is actually wired up on this site: fixtures applied,
    DocTypes present, hook registered and the device configured. Run this first when
    a submit appears to do nothing at all.

        bench --site <site> execute tims_integration.services.rest.check_setup
    """
    report = {}

    expected_fields = [
        "custom_sent_to_kra", "custom_tims_response_code", "custom_cuin",
        "custom_cusn", "custom_tsin", "custom_kra_qr_code_data",
        "custom_kra_signing_time", "custom_taxation_type",
    ] + ["custom_tax_{0}".format(b) for b in "abcde"] \
      + ["custom_taxbl_amount_{0}".format(b) for b in "abcde"]

    existing = set(frappe.get_all(
        "Custom Field",
        filters={"dt": "Sales Invoice", "fieldname": ["in", expected_fields]},
        pluck="fieldname",
    ))
    report["missing_custom_fields"] = sorted(set(expected_fields) - existing)

    # A Custom Field row can exist while the column does not, if a migrate was
    # interrupted - check the table itself rather than trusting the metadata.
    columns = set(frappe.db.get_table_columns("Sales Invoice"))
    report["missing_columns"] = sorted(f for f in expected_fields if f not in columns)

    report["doctypes_present"] = {
        dt: frappe.db.exists("DocType", dt) is not None
        for dt in ("KRA Response", "TIMS Device Setup")
    }

    hooks = frappe.get_hooks("doc_events") or {}
    report["on_submit_hook"] = (hooks.get("Sales Invoice") or {}).get("on_submit")

    device_setup = frappe.get_single("TIMS Device Setup")
    report["device"] = {
        "status": device_setup.status,
        "ip": device_setup.ip,
        "port": device_setup.port,
        "till_number": device_setup.till_number,
        "send_invoices_to_kra_on_submit": device_setup.send_invoices_to_kra_on_submit,
        "send_credit_notes": device_setup.send_credit_notes,
        "allow_other_day_posting": device_setup.allow_other_day_posting,
        "allow_submission_on_failure": device_setup.allow_submission_on_failure,
    }

    report["kra_responses_logged"] = frappe.db.count("KRA Response")

    return report
