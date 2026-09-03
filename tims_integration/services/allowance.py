"""
What a TIMS invoice has left to be credited.

Read from the payloads we actually sent, recorded on KRA Response. That is the
only account that reflects the encoding we chose rather than what ERPNext happens
to display, and it counts credit notes ERPNext knows nothing about -- a price
adjustment deliberately leaves ``return_against`` blank so ERPNext does not
charge it against the original's return quantity.

Historic rows stored the payload as a Python ``repr`` rather than JSON, so both
are parsed: new rows are JSON, old ones fall back to a *literal* eval, which
evaluates only Python literals and cannot execute anything.
"""

import ast
import json
from decimal import Decimal

import frappe
from frappe import _

ZERO = Decimal("0")


def _dec(value):
	if isinstance(value, Decimal):
		return value
	return Decimal(str(value or 0))


def _line_key(line):
	"""
	Match a refund line to the sale line it adjusts.

	Description first, because ``productCode`` is *not* unique: exempt and
	zero-rated lines all carry the same static KRA HS code, so keying on it alone
	would merge unrelated items into one budget.
	"""
	desc = str(line.get("productDesc") or "").strip()
	return desc or str(line.get("productCode") or "").strip()


def parse_payload(raw):
	"""Payload -> dict, tolerating both the JSON and the legacy repr format."""
	if not raw:
		return {}
	if isinstance(raw, dict):
		return raw

	text = str(raw).strip()
	for parse in (json.loads, ast.literal_eval):
		try:
			value = parse(text)
		except (TypeError, ValueError, SyntaxError):
			continue
		# A payload is an object. Valid JSON that is a list or a bare scalar is
		# not one, and returning it would only fail later on .get().
		if isinstance(value, dict):
			return value
	return {}


def _response_lines(invoice, sale_type):
	"""
	Accepted payloads for an invoice. ``ResponseCode`` 000 is the only success, so
	a rejected attempt never consumes budget.
	"""
	rows = frappe.get_all(
		"KRA Response",
		filters={"invoice_number": invoice, "response_code": "000"},
		fields=["name", "payload_sent"],
		order_by="creation asc",
	)

	lines = []
	for row in rows:
		payload = parse_payload(row.payload_sent)
		if not payload:
			continue
		if str(payload.get("saleType") or "").lower() != sale_type:
			continue
		lines.extend(payload.get("data") or [])
	return lines


def original_lines(invoice):
	"""What we declared for the sale, keyed by line."""
	lines = {}
	for line in _response_lines(invoice, "sales"):
		key = _line_key(line)
		if not key:
			continue
		entry = lines.setdefault(
			key, {"description": line.get("productDesc") or key, "unit_price": ZERO, "qty": ZERO, "amount": ZERO}
		)
		unit_price = _dec(line.get("unitPrice"))
		qty = _dec(line.get("quantity"))
		discount = _dec(line.get("discount"))
		entry["unit_price"] = max(entry["unit_price"], unit_price)
		entry["qty"] += qty
		entry["amount"] += unit_price * qty - discount
	return lines


def credit_notes_against(invoice):
	"""
	Every credit note pointing at this invoice, through either field.

	``custom_tims_original_invoice`` carries the reference for a price adjustment
	(where ``return_against`` is blank on purpose); ``return_against`` carries it
	for a goods return.
	"""
	invoice_table = frappe.qb.DocType("Sales Invoice")
	return (
		frappe.qb.from_(invoice_table)
		.select(invoice_table.name)
		.where(
			(invoice_table.docstatus == 1)
			& (invoice_table.is_return == 1)
			& (
				(invoice_table.custom_tims_original_invoice == invoice)
				| (
					(invoice_table.return_against == invoice)
					& (
						invoice_table.custom_tims_original_invoice.isnull()
						| (invoice_table.custom_tims_original_invoice == "")
					)
				)
			)
		)
	).run(pluck=True)


def credited_lines(invoice):
	credited = {}
	for note in credit_notes_against(invoice):
		for line in _response_lines(note, "refund"):
			key = _line_key(line)
			if not key:
				continue
			entry = credited.setdefault(key, {"qty": ZERO, "amount": ZERO})
			unit_price = _dec(line.get("unitPrice"))
			qty = _dec(line.get("quantity"))
			entry["qty"] += qty
			entry["amount"] += unit_price * qty - _dec(line.get("discount"))
	return credited


def original_invoice_of(credit_note):
	"""The invoice a credit note adjusts, whichever field carries it."""
	row = frappe.db.get_value(
		"Sales Invoice", credit_note, ["custom_tims_original_invoice", "return_against"], as_dict=True
	)
	if not row:
		return None
	return row.custom_tims_original_invoice or row.return_against


def remaining(invoice):
	originals = original_lines(invoice)
	credited = credited_lines(invoice)

	result = {}
	for key, entry in originals.items():
		used = credited.get(key, {"qty": ZERO, "amount": ZERO})
		result[key] = {
			"description": entry["description"],
			"unit_price": entry["unit_price"],
			"original_qty": entry["qty"],
			"original_amount": entry["amount"],
			"credited_qty": used["qty"],
			"credited_amount": used["amount"],
			"remaining_qty": entry["qty"] - used["qty"],
			"remaining_amount": entry["amount"] - used["amount"],
		}
	return result


def summary(invoice):
	lines = remaining(invoice)
	if not lines:
		return None
	return {
		"invoice": invoice,
		"original_amount": sum(v["original_amount"] for v in lines.values()),
		"credited_amount": sum(v["credited_amount"] for v in lines.values()),
		"remaining_amount": sum(v["remaining_amount"] for v in lines.values()),
		"lines": lines,
	}


@frappe.whitelist()
def get_allowance(invoice):
	frappe.has_permission("Sales Invoice", "read", doc=invoice, throw=True)

	data = summary(invoice)
	if not data:
		return {
			"available": False,
			"message": _("{0} has no accepted TIMS sale recorded, so nothing can be credited against it yet.").format(invoice),
		}

	return {
		"available": True,
		"invoice": invoice,
		"original_amount": str(data["original_amount"]),
		"credited_amount": str(data["credited_amount"]),
		"remaining_amount": str(data["remaining_amount"]),
		"lines": [
			{
				"description": v["description"],
				"unit_price": str(v["unit_price"]),
				"original_qty": str(v["original_qty"]),
				"original_amount": str(v["original_amount"]),
				"credited_qty": str(v["credited_qty"]),
				"remaining_qty": str(v["remaining_qty"]),
				"remaining_amount": str(v["remaining_amount"]),
			}
			for v in data["lines"].values()
		],
	}
