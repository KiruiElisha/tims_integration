"""
TIMS price-adjustment declaration.

A Price Adjustment line is declared to TIMS exactly as the user entered it -
quantity and discount, at the unit price the original invoice actually
declared - never recalculated from a target money value. See
tims_integration.services.rest.encode_declared_adjustment_line.

Run with::

    ./env/bin/python -m unittest discover \\
        -s apps/tims_integration/tims_integration/tests -t apps/tims_integration
"""

import unittest
from decimal import Decimal

from tims_integration.services.allowance import parse_payload

UNIT = Decimal("580.00")


class TestPayloadCompatibility(unittest.TestCase):
	"""
	Payloads used to be stored as a Python repr and are now JSON. The allowance
	figures are read from these rows, so both formats have to parse or an invoice
	predating the change would look as though nothing had ever been credited.
	"""

	def test_reads_json(self):
		payload = parse_payload('{"saleType": "sales", "data": [{"productDesc": "A"}]}')
		self.assertEqual(payload["saleType"], "sales")
		self.assertEqual(payload["data"][0]["productDesc"], "A")

	def test_reads_legacy_repr(self):
		legacy = str({"saleType": "sales", "data": [{"productDesc": "A", "unitPrice": 580.0}]})
		payload = parse_payload(legacy)
		self.assertEqual(payload["saleType"], "sales")
		self.assertEqual(payload["data"][0]["unitPrice"], 580.0)

	def test_garbage_yields_empty_not_an_exception(self):
		# An unreadable row must understate the allowance, never abort the submit.
		for bad in ("", None, "not a payload", "{unclosed", "[1,2,3]"):
			self.assertEqual(parse_payload(bad), {})

	def test_literal_eval_does_not_execute(self):
		# The legacy fallback evaluates literals only.
		self.assertEqual(parse_payload("__import__('os').system('true')"), {})


class TestDeclaredAdjustmentLine(unittest.TestCase):
	"""
	encode_declared_adjustment_line copies qty/TIMS unit price/TIMS discount
	straight off the row - it looks nothing up and derives nothing, so the
	document and the payload can never disagree.
	"""

	def _base_item(self):
		return {"productCode": "X", "productDesc": "Widget", "quantity": 100.0,
		        "unitPrice": 58.0, "discount": 0.0, "taxtype": "16"}

	def test_declared_qty_and_discount_are_sent_as_entered(self):
		from tims_integration.services.rest import encode_declared_adjustment_line

		row = {"qty": -400, "custom_tims_unit_price": UNIT, "custom_tims_discount": 50}

		out = encode_declared_adjustment_line(self._base_item(), row)

		self.assertEqual(out["quantity"], 400.0)
		self.assertEqual(out["discount"], 50.0)
		self.assertEqual(out["unitPrice"], float(UNIT))
		# The source line must not be mutated.
		self.assertEqual(self._base_item()["quantity"], 100.0)

	def test_no_tims_unit_price_leaves_line_untouched(self):
		from tims_integration.services.rest import encode_declared_adjustment_line

		base = self._base_item()
		out = encode_declared_adjustment_line(base, {"qty": -3})
		self.assertEqual(out, base)


if __name__ == "__main__":
	unittest.main()
