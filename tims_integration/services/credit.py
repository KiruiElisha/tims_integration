"""
Price adjustments on TIMS.

Same user-facing workflow as the eTIMS app, so the two can be tested against the
same documents -- but the reasoning behind the encoding is *not* the same, and the
difference is worth stating rather than copying across silently.

eTIMS validates a credit note against the invoice it adjusts: unit price must not
exceed the original (E219), and credited quantity accumulates against the
original's quantity (E220). That makes quantity a finite budget, and encoding at
the original unit price with a fractional quantity is what keeps repeated
adjustments possible.

The LDL/Aclas TIMS specification (v1.0) documents **no such validation**. A refund
is ``saleType: refund`` plus the original CUIN; nothing in the spec compares
quantity or price against the original sale, and errors come back only as a
generic ``ResponseCode 500``. So on TIMS the quantity budget is, as documented,
not a constraint at all.

We use the identical encoding anyway, for two reasons:

* it makes a TIMS test and an eTIMS test exercise the same documents and the same
  payload shape, which is the point of being able to run both;
* the spec is a thin six-page document from 2022 and absence of a documented
  check is not evidence of an absent check. Encoding conservatively costs nothing.

What we do *not* do is copy eTIMS's hard block on the quantity budget -- see
:func:`check_fits`.
"""

from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

QTY_PLACES = Decimal("0.01")  # TIMS quantity is "with 2 decimals" per the spec
MONEY_PLACES = Decimal("0.01")

ZERO = Decimal("0")

# How a price adjustment line is expressed. Set per device.
ENCODING_FRACTIONAL = "Original Price & Fractional Quantity"
ENCODING_ZERO_QTY = "Zero Quantity"


class AdjustmentTooLarge(Exception):
	def __init__(self, requested, available, dimension):
		self.requested = requested
		self.available = available
		self.dimension = dimension
		super().__init__(f"{dimension}: need {requested}, {available} remaining")


def encode(target_value, original_unit_price, qty_places=QTY_PLACES):
	"""
	Express a credit of ``target_value`` as (unit price, quantity, discount) with::

	    unit_price * quantity - discount == target_value      (exactly, to the cent)

	The unit price is the original's, which both minimises quantity consumed and
	keeps the refund line recognisably the same product at the same price.

	Quantity rounds *up* to the device's two decimals and the excess goes into the
	discount field, which TIMS carries per line. Rounding down would credit less
	than the accounting entry, and a discount cannot be negative.
	"""
	target_value = _money(target_value)
	original_unit_price = _money(original_unit_price)

	if target_value <= ZERO:
		return original_unit_price, ZERO, ZERO

	if original_unit_price <= ZERO:
		raise ValueError("Cannot encode a price adjustment against a zero original unit price.")

	quantity = (target_value / original_unit_price).quantize(qty_places, rounding=ROUND_CEILING)
	gross = _money(original_unit_price * quantity)
	discount = gross - target_value

	return original_unit_price, quantity, discount


def quantity_needed(target_value, original_unit_price, qty_places=QTY_PLACES):
	return encode(target_value, original_unit_price, qty_places)[1]


def check_fits(target_value, original_unit_price, remaining_qty, remaining_value, qty_places=QTY_PLACES):
	"""
	Returns ``(quantity_needed, warnings)``.

	Value is the hard limit: crediting more than was invoiced is wrong as
	accounting regardless of what any device accepts, so the caller refuses it.

	Quantity is only *warned* about here, unlike the eTIMS app. TIMS documents no
	quantity ceiling, and hard-blocking on a limit this device may not have would
	stop the very test that establishes whether it does.
	"""
	target_value = _money(target_value)
	warnings = []

	if target_value > _money(remaining_value):
		raise AdjustmentTooLarge(target_value, _money(remaining_value), "value")

	needed = quantity_needed(target_value, original_unit_price, qty_places)
	if needed > Decimal(str(remaining_qty)):
		warnings.append(
			f"This adjustment uses {needed} of a quantity the original invoice has only "
			f"{remaining_qty} left for. TIMS does not document a quantity limit on refunds, "
			f"so this is allowed through - but the same document would be rejected by eTIMS."
		)

	return needed, warnings


def _money(value):
	if not isinstance(value, Decimal):
		value = Decimal(str(value or 0))
	return value.quantize(MONEY_PLACES, rounding=ROUND_HALF_UP)


def encode_zero_quantity(target_value, original_unit_price):
	"""
	Express the adjustment as a zero-quantity line.

	This works on TIMS for a structural reason, not by luck. A TIMS line is
	``productCode / productDesc / quantity / unitPrice / discount / taxtype`` --
	there is **no per-line amount field** -- and ``total`` is built in
	:func:`rest.create_payload` purely from the VAT band figures, never from the
	lines. The declared value therefore travels in ``total`` and ``VAT_x``, and
	zeroing a line's quantity does not reduce it.

	It is the more honest shape for a price concession: no goods moved, so no
	quantity is claimed, and the money is declared where TIMS actually reads it.

	**This cannot be used on eTIMS.** There each line carries its own
	``SaleAmount``, and the device checks both ``SalePrice x SaleQty == SaleAmount``
	(E322) and that the lines re-add to ``sign_structure`` (E341). A zero-quantity
	line is either worth nothing or fails those checks, which is why the eTIMS app
	encodes with a fractional quantity instead.

	Returns ``(unit_price, quantity, discount)`` for symmetry with :func:`encode`.
	"""
	return _money(original_unit_price), ZERO, ZERO


def encode_for(encoding, target_value, original_unit_price, qty_places=QTY_PLACES):
	"""Dispatch to the encoding this device is configured for."""
	if encoding == ENCODING_ZERO_QTY:
		return encode_zero_quantity(target_value, original_unit_price)
	return encode(target_value, original_unit_price, qty_places)
