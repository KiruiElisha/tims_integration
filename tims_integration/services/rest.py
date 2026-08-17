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

        if device_setup.status == 'Active':
            if is_valid_posting_date(doc, device_setup):
                payload = build_payload(doc, device_setup)
                send_payload(payload, invoice, doc)
            else:
                frappe.msgprint(
                    msg="Invoice Posting Date Must be Today's Date",
                    title='Error Message',
                    indicator='red',
                )
        else:
            frappe.msgprint(
                msg='TIMS Device Setup for Sending Invoices is not Active.',
                title='Error Message',
                indicator='red',
            )
    except Exception as e:
        handle_exception(e)


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
    customer_pin = frappe.db.get_value("Customer", doc.customer, "tax_id") or ''
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

    is_exempt = category == "exempt"
    product_code = get_hs_code(item.item_code, category)

    new_item = {
        "productCode": product_code,
        "productDesc": item.item_name,
        "quantity": abs(float(qty)),
        "unitPrice": abs(float(unit_price)),
        "discount": abs(float(discount)),
        "taxtype": "exempted" if is_exempt else category,
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
    cuin = "" if not doc.is_return else frappe.db.get_value("KRA Response", {"invoice_number": doc.name}, "cuin")

    payload = {
        "saleType": payload_type,
        "cuin": cuin,
        "till": till_no,
        "rctNo": rct_no,
        "total": round(float(total), 2),
        "Paid": round(float(total), 2),
        "Payment": payment_method,
        "CustomerPIN": customer_pin,
        "VAT_A_Net": round(float(vat_values["VAT_A_NET"]), 2),
        "VAT_A": round(float(vat_values["VAT_A"]), 2),
        "VAT_B_Net": round(float(vat_values["VAT_B_NET"]), 2),
        "VAT_B": round(float(vat_values["VAT_B"]), 2),
        "VAT_C_Net": round(float(vat_values["VAT_C_NET"]), 2),
        "VAT_C": round(float(vat_values["VAT_C"]), 2),
        "VAT_D_Net": round(float(vat_values["VAT_D_NET"]), 2),
        "VAT_D": round(float(vat_values["VAT_D"]), 2),
        "VAT_E_Net": round(float(vat_values["VAT_E_NET"]), 2),
        "VAT_E": round(float(vat_values["VAT_E"]), 2),
        "VAT_F_Net": round(float(vat_values["VAT_F_NET"]), 2),
        "VAT_F": round(float(vat_values["VAT_F"]), 2),
        "data": items
    }

    return payload


def send_payload(payload, invoice, doc):
    try:
        device_setup = frappe.get_single('TIMS Device Setup')
        response = requests.post(
            f"http://{device_setup.ip}:{device_setup.port}/api/values/PostTims",
            json=payload,
            timeout=60
        )
        handle_response(response, invoice, doc, payload)
    except Exception as e:
        frappe.msgprint(
            msg="Request Time Out Error, please make sure the TIMS/ETR Machine is active.",
            title="Error Message",
            indicator='red',
        )


def handle_response(response, invoice, doc, payload):
    data = json.loads(response.text)

    kra_response = frappe.get_doc({
        "doctype": "KRA Response",
        "response_code": data["ResponseCode"] or '',
        "message": data["Message"],
        "tin": data["TSIN"],
        "cusn": data["CUSN"],
        "cuin": data["CUIN"],
        "qr_code": data["QRCode"],
        "signing_time": data["dtStmp"],
        "invoice_number": invoice,
        "payload_sent": str(payload)
    })

    kra_response.insert()
    frappe.db.commit()

    if data['ResponseCode'] == '000':
        update_doc_with_response(doc, data)
    else:
        frappe.msgprint(
            msg="Invoice Submission to KRA Failed. Please Check KRA Response Generated.",
            title='Error Message',
            indicator='red',
        )


def update_doc_with_response(doc, data):
    doc.custom_tims_response_code = data["ResponseCode"]
    doc.custom_tsin = data["TSIN"]
    doc.custom_cusn = data["CUSN"]
    doc.custom_cuin = data["CUIN"]
    doc.custom_kra_qr_code_data = data["QRCode"]
    doc.custom_kra_signing_time = data["dtStmp"]
    doc.custom_sent_to_kra = 1
    doc.save(ignore_permissions=True)

    if doc.docstatus == 0:
        doc.submit()
        doc.reload()


def handle_exception(exception):
    error_message = "TIMS KRA Error.\n{}".format(frappe.get_traceback())
    frappe.log_error(error_message, "TIMS KRA Error.")
    frappe.msgprint(
        msg="Something Wrong, Please try again or check the "+"<a style='color: red; font-weight: bold;' href='/app/error-log'>Error Logs</a>",
        title="Error Message",
        indicator='red',
    )
    return exception
