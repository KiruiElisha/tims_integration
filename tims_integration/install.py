"""
Custom fields for TIMS, created in code.

Previously these were shipped as a fixture filtered on
``["name", "like", "Sales Invoice-custom_%"]``. That filter matches *any* app's
custom fields on Sales Invoice, so the exported file ended up holding another
app's fields and none of this app's -- meaning a fresh install got no TIMS fields
at all, which is what ``rest.check_setup()`` reports as ``missing_custom_fields``.

``create_custom_fields`` is idempotent and is called with ``update=False``, so it
creates what is absent and never disturbs a field an existing site already has.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

ADJUSTMENT_TYPES = "\nGoods Return\nPrice Adjustment"

CUSTOM_FIELDS = {
	"Sales Invoice": [
		{
			"fieldname": "custom_tims_section",
			"label": "TIMS",
			"fieldtype": "Section Break",
			"insert_after": "taxes_and_charges",
			"collapsible": 1,
		},
		# ---- credit note handling ------------------------------------------
		{
			"fieldname": "custom_tims_adjustment_type",
			"label": "TIMS Credit Note Type",
			"fieldtype": "Select",
			"options": ADJUSTMENT_TYPES,
			"insert_after": "custom_tims_section",
			"default": "Goods Return",
			"depends_on": "eval:doc.is_return",
			"mandatory_depends_on": "eval:doc.is_return",
			"description": "Goods Return: stock comes back. Price Adjustment: no goods move, "
			"and the refund is encoded at the original unit price with a fractional quantity "
			"so the same invoice can be adjusted again as prices move.",
		},
		{
			"fieldname": "custom_tims_original_invoice",
			"label": "TIMS Original Invoice",
			"fieldtype": "Link",
			"options": "Sales Invoice",
			"insert_after": "custom_tims_adjustment_type",
			"depends_on": "eval:doc.is_return",
			"mandatory_depends_on": "eval:doc.is_return && doc.custom_tims_adjustment_type=='Price Adjustment'",
			"description": "The invoice being adjusted. Kept separate from 'Return Against' on "
			"purpose: ERPNext counts a return against the original's quantity, which would "
			"block every later price adjustment.",
		},
		# ---- fiscal results -------------------------------------------------
		{
			"fieldname": "custom_tims_column_break",
			"fieldtype": "Column Break",
			"insert_after": "custom_tims_original_invoice",
		},
		{
			"fieldname": "custom_sent_to_kra",
			"label": "Sent to KRA",
			"fieldtype": "Check",
			"insert_after": "custom_tims_column_break",
			"read_only": 1,
			"allow_on_submit": 1,
			"no_copy": 1,
		},
		{
			"fieldname": "custom_tims_response_code",
			"label": "TIMS Response Code",
			"fieldtype": "Data",
			"insert_after": "custom_sent_to_kra",
			"read_only": 1,
			"allow_on_submit": 1,
			"no_copy": 1,
		},
		{
			"fieldname": "custom_cuin",
			"label": "CUIN",
			"fieldtype": "Data",
			"insert_after": "custom_tims_response_code",
			"read_only": 1,
			"allow_on_submit": 1,
			"no_copy": 1,
		},
		{
			"fieldname": "custom_cusn",
			"label": "CUSN",
			"fieldtype": "Data",
			"insert_after": "custom_cuin",
			"read_only": 1,
			"allow_on_submit": 1,
			"no_copy": 1,
		},
		{
			"fieldname": "custom_tsin",
			"label": "TSIN",
			"fieldtype": "Data",
			"insert_after": "custom_cusn",
			"read_only": 1,
			"allow_on_submit": 1,
			"no_copy": 1,
		},
		{
			"fieldname": "custom_kra_qr_code_data",
			"label": "KRA QR Code Data",
			"fieldtype": "Small Text",
			"insert_after": "custom_tsin",
			"read_only": 1,
			"allow_on_submit": 1,
			"no_copy": 1,
		},
		{
			"fieldname": "custom_kra_signing_time",
			"label": "KRA Signing Time",
			"fieldtype": "Date",
			"insert_after": "custom_kra_qr_code_data",
			"read_only": 1,
			"allow_on_submit": 1,
			"no_copy": 1,
		},
		{
			"fieldname": "custom_taxation_type",
			"label": "Taxation Type",
			"fieldtype": "Data",
			"insert_after": "custom_kra_signing_time",
			"read_only": 1,
			"allow_on_submit": 1,
			"no_copy": 1,
		},
	]
}

# The per-band net/tax pair mirrored onto the invoice after a successful send.
for _band in "abcde":
	CUSTOM_FIELDS["Sales Invoice"].extend(
		[
			{
				"fieldname": f"custom_taxbl_amount_{_band}",
				"label": f"Taxable Amount {_band.upper()}",
				"fieldtype": "Currency",
				"insert_after": "custom_taxation_type",
				"read_only": 1,
				"allow_on_submit": 1,
				"no_copy": 1,
				"hidden": 1,
			},
			{
				"fieldname": f"custom_tax_{_band}",
				"label": f"Tax {_band.upper()}",
				"fieldtype": "Currency",
				"insert_after": f"custom_taxbl_amount_{_band}",
				"read_only": 1,
				"allow_on_submit": 1,
				"no_copy": 1,
				"hidden": 1,
			},
		]
	)


def after_install():
	setup()


def after_migrate():
	setup()


def setup():
	# update=False: create what is missing, leave every existing field exactly as
	# the site has it. These fields are live on production sites and a migrate
	# must not rewrite their properties.
	create_custom_fields(CUSTOM_FIELDS, ignore_validate=True, update=False)
