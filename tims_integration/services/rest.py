import frappe
import json
import requests
from frappe.utils import today
from datetime import datetime

@frappe.whitelist()
def send_request(invoice):
    try:
        device_setup = frappe.get_single('TIMS Device Setup')
        doc = frappe.get_doc("Sales Invoice", invoice)

        if device_setup.status != 'Active':
            skip(invoice, "TIMS Device Setup status is {0}, not Active.".format(
                device_setup.status))
            return

        if not is_valid_posting_date(doc, device_setup):
            skip(invoice, "Posting date {0} is not today and 'Allow Other Day "
                          "Posting' is off.".format(doc.posting_date))
            return

        payload = build_payload(doc, device_setup)
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


def is_valid_posting_date(doc, device_setup):
    today = datetime.now().strftime("%d-%m-%Y")
    posting_date = doc.posting_date.strftime("%d-%m-%Y")
    return posting_date == today or device_setup.allow_other_day_posting


def build_payload(doc, device_setup):
    payment_method = "Cash" if doc.status == 'Paid' else 'Credit'
    # Blank unless a till is explicitly configured: the device prefixes a set till
    # (e.g. "01") to the invoice number on the fiscal receipt.
    till_no = str(device_setup.till_number or "").strip()
    rct_no = doc.name
    customer_pin = get_customer_pin(doc)
    invoice_items = get_invoice_items(doc.name)
    default_tax = get_default_invoice_tax(doc.name)

    vat_values = initialize_vat_values()
    items = []

    for item in invoice_items:
        tax_title = item.tax_title
        tax_rate = item.tax_rate
        if not tax_title:
            tax_title, tax_rate = default_tax

        new_item, taxable_amount, tax_amount, category = calculate_tax(item, tax_title, tax_rate)
        vat_values = update_vat_values(vat_values, category, taxable_amount, tax_amount)
        items.append(new_item)

    payload = create_payload(doc, vat_values, items, payment_method, customer_pin, till_no, rct_no)
    return payload


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


def get_invoice_items(invoice):
    query = """
        SELECT sii.name, sii.item_code, sii.item_name, sii.rate, sii.base_rate, sii.base_amount,
        sii.base_net_rate, sii.base_net_amount, sii.qty, sii.item_tax_template,
        it_template.title AS tax_title, it_template_detail.tax_rate AS tax_rate
        FROM `tabSales Invoice Item` sii
        LEFT JOIN `tabItem Tax Template` it_template ON it_template.name = sii.item_tax_template
        LEFT JOIN `tabItem Tax Template Detail` it_template_detail ON it_template_detail.parent = sii.item_tax_template
        WHERE sii.parent = %s
    """
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


def calculate_tax(item, tax_title, tax_rate):
    category, rate = classify_tax(tax_title, tax_rate)
    if category is None:
        frappe.log_error(
            title="TIMS KRA: Unclassified item tax",
            message="Item {0} on invoice {1} has no resolvable tax template or "
                     "invoice-level tax row; defaulting to the standard 16% VAT band.".format(
                         item.item_code, item.name)
        )
        category, rate = "16", 16.0

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

    return new_item, taxable_amount, tax_amount, category


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


def get_original_cuin(doc):
    """
    A refund must quote the CUIN of the invoice it reverses, which lives on the
    original invoice's KRA Response - not on the credit note itself.
    """
    original = doc.return_against
    if not original:
        frappe.throw("Credit note {0} has no Return Against invoice, so its "
                     "original KRA CUIN cannot be determined.".format(doc.name))

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


def record_kra_response(data, invoice, payload):
    """
    Persists the exchange. Runs in its own savepoint and swallows nothing quietly:
    if the row cannot be written we still want the raw device reply in the Error Log,
    otherwise a successful fiscalisation leaves no trace anywhere.
    """
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
            "payload_sent": str(payload)
        })
        kra_response.insert(ignore_permissions=True)
        frappe.db.commit()
        return kra_response.name
    except Exception:
        frappe.db.rollback()
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
        report["payload"] = build_payload(doc, device_setup)

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
