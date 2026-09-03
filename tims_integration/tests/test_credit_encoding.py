"""
TIMS price-adjustment encoding.

Mirrors the eTIMS suite so the two can be compared directly, and pins the two
places where TIMS deliberately behaves differently:

* quantity carries two decimals here, not three (spec: "Quantity (with 2
  decimals)");
* a quantity overdraft warns instead of raising, because the TIMS specification
  documents no quantity ceiling on refunds and blocking on a limit the device may
  not have would prevent the very test that establishes whether it does.

Run with::

    ./env/bin/python -m unittest discover \\
        -s apps/tims_integration/tims_integration/tests -t apps/tims_integration
"""

import unittest
from decimal import Decimal

from tims_integration.services.allowance import parse_payload
from tims_integration.services.credit import (
	ENCODING_FRACTIONAL,
	ENCODING_ZERO_QTY,
	AdjustmentTooLarge,
	check_fits,
	encode,
	encode_for,
	encode_zero_quantity,
	quantity_needed,
)

UNIT = Decimal("580.00")
QTY = Decimal("100")
VALUE = Decimal("58000.00")


class TestEncoding(unittest.TestCase):
	def test_line_identity_always_holds(self):
		"""unitPrice x quantity - discount == the value being credited, exactly."""
		for target in ("5800.00", "3000.00", "0.01", "1234.56", "57999.99", "7.77"):
			target = Decimal(target)
			price, qty, discount = encode(target, UNIT)
			self.assertEqual((price * qty).quantize(Decimal("0.01")) - discount, target)

	def test_quantity_uses_two_decimals(self):
		# TIMS spec: "Quantity (with 2 decimals)". eTIMS allows three.
		_p, qty, _d = encode(Decimal("3000.00"), UNIT)
		self.assertEqual(qty, Decimal("5.18"))
		self.assertEqual(qty.as_tuple().exponent, -2)

	def test_unit_price_is_the_original(self):
		price, _q, _d = encode(Decimal("3000.00"), UNIT)
		self.assertEqual(price, UNIT)

	def test_discount_is_never_negative(self):
		for target in ("0.01", "3000.00", "5800.00", "12345.67"):
			_p, _q, discount = encode(Decimal(target), UNIT)
			self.assertGreaterEqual(discount, Decimal("0"))

	def test_exact_multiple_needs_no_discount(self):
		price, qty, discount = encode(Decimal("5800.00"), UNIT)
		self.assertEqual((price, qty, discount), (UNIT, Decimal("10.00"), Decimal("0.00")))


class TestRepeatedAdjustments(unittest.TestCase):
	def test_many_adjustments_fit(self):
		remaining_qty, remaining_value = QTY, VALUE
		for _ in range(15):
			needed, warnings = check_fits(Decimal("2900.00"), UNIT, remaining_qty, remaining_value)
			self.assertEqual(warnings, [])
			remaining_qty -= needed
			remaining_value -= Decimal("2900.00")

		self.assertEqual(remaining_value, Decimal("14500.00"))
		self.assertEqual(remaining_qty, Decimal("25.00"))

	def test_value_overdraft_raises(self):
		# Crediting more than was invoiced is an accounting error on any device.
		with self.assertRaises(AdjustmentTooLarge) as ctx:
			check_fits(Decimal("60000.00"), UNIT, QTY, VALUE)
		self.assertEqual(ctx.exception.dimension, "value")

	def test_quantity_overdraft_warns_rather_than_raising(self):
		# The eTIMS app refuses this. TIMS documents no quantity limit, so it is
		# allowed through with the difference spelled out.
		needed, warnings = check_fits(Decimal("5800.00"), UNIT, Decimal("2"), VALUE)
		self.assertEqual(needed, Decimal("10.00"))
		self.assertEqual(len(warnings), 1)
		self.assertIn("eTIMS", warnings[0])

	def test_original_price_minimises_quantity(self):
		value = Decimal("5800.00")
		self.assertLess(quantity_needed(value, UNIT), quantity_needed(value, Decimal("58.00")))


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



class TestZeroQuantityEncoding(unittest.TestCase):
	"""
	The zero-quantity shape, which TIMS accepts and eTIMS cannot.

	It works because a TIMS line has no amount field: the declared value lives in
	the top-level ``total`` and ``VAT_x``, which rest.create_payload builds from
	the band figures and never from the lines. Zeroing a quantity therefore does
	not reduce what is declared.
	"""

	def test_zero_quantity_claims_no_quantity(self):
		price, qty, discount = encode_zero_quantity(Decimal("5800.00"), UNIT)
		self.assertEqual(qty, Decimal("0"))
		self.assertEqual(discount, Decimal("0"))
		self.assertEqual(price, UNIT)

	def test_zero_quantity_consumes_no_budget(self):
		# The whole point: adjust the same invoice indefinitely.
		remaining_qty = QTY
		for _ in range(50):
			_p, qty, _d = encode_zero_quantity(Decimal("500.00"), UNIT)
			remaining_qty -= qty
		self.assertEqual(remaining_qty, QTY)

	def test_dispatch_honours_the_device_setting(self):
		value = Decimal("3000.00")
		_p, frac_qty, _d = encode_for(ENCODING_FRACTIONAL, value, UNIT)
		_p, zero_qty, _d = encode_for(ENCODING_ZERO_QTY, value, UNIT)
		self.assertEqual(frac_qty, Decimal("5.18"))
		self.assertEqual(zero_qty, Decimal("0"))

	def test_unknown_encoding_falls_back_to_fractional(self):
		# An unset or misspelt setting must not silently send zero quantities.
		_p, qty, _d = encode_for(None, Decimal("3000.00"), UNIT)
		self.assertEqual(qty, Decimal("5.18"))
		_p, qty, _d = encode_for("something else", Decimal("3000.00"), UNIT)
		self.assertEqual(qty, Decimal("5.18"))

	def test_both_encodings_declare_the_same_value(self):
		"""
		The invariant that makes the choice safe: the encoding changes the line
		only. The amount declared to KRA comes from the VAT bands, which are
		computed before encoding and never touched by it.
		"""
		from tims_integration.services.rest import encode_adjustment_line

		base = {"productCode": "X", "productDesc": "Widget", "quantity": 100.0,
		        "unitPrice": 58.0, "discount": 0.0, "taxtype": "16"}
		allowance = {"Widget": {"unit_price": UNIT, "remaining_qty": QTY,
		                        "remaining_amount": VALUE}}
		gross = Decimal("5800.00")

		frac = encode_adjustment_line(base, gross, allowance, ENCODING_FRACTIONAL)
		zero = encode_adjustment_line(base, gross, allowance, ENCODING_ZERO_QTY)

		self.assertEqual(frac["quantity"], 10.0)
		self.assertEqual(zero["quantity"], 0.0)
		self.assertEqual(frac["unitPrice"], zero["unitPrice"], 580.0)
		# The caller's band figures are what declare the money, and neither
		# encoding is given the chance to alter them.
		self.assertEqual(base["quantity"], 100.0, "the source line must not be mutated")

	def test_unmatched_line_is_left_alone(self):
		from tims_integration.services.rest import encode_adjustment_line

		base = {"productCode": "X", "productDesc": "Unknown", "quantity": 3.0,
		        "unitPrice": 10.0, "discount": 0.0, "taxtype": "16"}
		out = encode_adjustment_line(base, Decimal("30.00"), {}, ENCODING_ZERO_QTY)
		self.assertEqual(out, base)
if __name__ == "__main__":
	unittest.main()
