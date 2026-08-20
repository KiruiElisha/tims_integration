import json

import frappe
from frappe.utils import flt


def get_line_taxes(doc):
    """
    Per-line tax for print formats, keyed by Sales Invoice Item name:
    {"<item row name>": {"rate": 16.0, "amount": 0.16}}

    The invoice-level custom_taxation_type is an aggregate of every band on the
    invoice, so it cannot be printed against an individual line - a zero-rated item
    on an invoice that also carries standard-rated items would show 16%.

    Resolved in order of reliability:
      1. Item Wise Tax Detail rows, which ERPNext computes per item per tax row.
      2. The item's own item_tax_rate, set from its Item Tax Template.
      3. The invoice's tax rows, which apply to every line equally.
    """
    lines = {}

    for row in (doc.get("item_wise_tax_details") or []):
        entry = lines.setdefault(row.item_row, {"rate": None, "amount": 0.0})
        # An item can be hit by more than one tax row: sum the amounts and report
        # the combined rate rather than whichever row happened to come last.
        entry["rate"] = flt(entry["rate"] or 0) + flt(row.rate)
        entry["amount"] = flt(entry["amount"]) + flt(row.amount)

    invoice_rate = sum(
        flt(t.rate) for t in (doc.get("taxes") or [])
        if t.charge_type in ("On Net Total", "On Previous Row Total")
    )

    for item in (doc.get("items") or []):
        if lines.get(item.name) and lines[item.name]["rate"] is not None:
            continue

        rate = _rate_from_item_tax_template(item)
        if rate is None:
            rate = invoice_rate

        lines[item.name] = {
            "rate": flt(rate),
            "amount": flt(item.net_amount) * flt(rate) / 100.0,
        }

    return lines


def _rate_from_item_tax_template(item):
    """
    item_tax_rate is a JSON map of tax account to rate, written from the item's Item
    Tax Template. An explicit 0 means zero-rated and must be preserved, so this
    returns None only when there is no template at all.
    """
    raw = item.get("item_tax_rate")
    if not raw:
        return None

    try:
        rates = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return None

    if not isinstance(rates, dict) or not rates:
        return None

    return sum(flt(v) for v in rates.values())


def get_line_discount(item):
    """
    Total discount given on the line. ERPNext stores discount_amount per unit, so it
    has to be multiplied out; falls back to the percentage when only that is set.
    """
    # discount_amount is derived as price_list_rate - rate, so it goes negative when
    # the rate is above the list price. That is a markup, not a discount, and must
    # not be printed in a discount column.
    discount = flt(item.discount_amount) * flt(item.qty)
    if discount > 0:
        return discount

    if flt(item.discount_percentage) > 0 and flt(item.price_list_rate) > 0:
        return flt(item.price_list_rate) * flt(item.qty) * flt(item.discount_percentage) / 100.0

    return 0.0
