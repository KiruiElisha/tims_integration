# Copyright (c) 2024, RONOH and contributors
# For license information, please see license.txt

from frappe.model.document import Document
import frappe
import socket  # Example for testing connection
from frappe.utils import now_datetime

class TIMSDeviceSetup(Document):
	pass


@frappe.whitelist()
def test_connection(ip, port, name):
	try:
		# Test the connection
		with socket.create_connection((ip, int(port)), timeout=5) as sock:
			# Update the document status
			doc = frappe.get_doc("TIMS Device Setup", name)
			doc.status = "Active"
			doc.save(ignore_permissions=True)
			frappe.db.commit()
			
			return {
				"success": True,
				"message": "Connection successful",
				"status": "Active"
			}
	except Exception as e:
		# Update status to inactive on failure
		doc = frappe.get_doc("TIMS Device Setup", name)
		
		doc.status = "Inactive"
		doc.save(ignore_permissions=True)
		frappe.db.commit()
		
		error_message = f"TIMS Device Connection Test Error: {str(e)}\nIP: {ip}, Port: {port}"
		frappe.log_error(
			message=error_message,
			title="TIMS Device Connection Test Error"
		)
		
		return {
			"success": False,
			"error": str(e),
			"status": "Inactive"
		}


@frappe.whitelist()
def send_test_invoice(name):
	"""
	Creates and submits a real KES 1 Sales Invoice and pushes it through the normal
	TIMS submission flow, to verify the device/KRA endpoint end-to-end. This registers
	a genuine fiscal invoice with KRA and cannot be undone once sent.
	"""
	from tims_integration.services.rest import send_request

	device_setup = frappe.get_doc("TIMS Device Setup", name)
	if device_setup.status != "Active":
		frappe.throw("TIMS Device Setup must be Active before sending a test invoice.")

	company = frappe.defaults.get_global_default("company")
	if not company:
		frappe.throw("Set a default Company before sending a TIMS test invoice.")

	item_code = _ensure_test_item()
	customer = _ensure_test_customer()

	invoice = frappe.get_doc({
		"doctype": "Sales Invoice",
		"customer": customer,
		"company": company,
		"update_stock": 0,
		"remarks": "TIMS connectivity test invoice - not a real sale",
		"items": [{
			"item_code": item_code,
			"qty": 1,
			"rate": 1,
		}],
	})
	invoice.insert(ignore_permissions=True)
	invoice.submit()
	frappe.db.commit()

	send_request(invoice.name)

	invoice = frappe.get_doc("Sales Invoice", invoice.name)
	return {
		"invoice": invoice.name,
		"sent_to_kra": invoice.custom_sent_to_kra,
		"response_code": invoice.custom_tims_code,
	}


@frappe.whitelist()
def send_test_return(sales_invoice):
	"""
	Creates and submits a full credit note against the given (already-sent) test
	invoice and pushes it through the normal TIMS submission flow as a refund.
	This registers a genuine fiscal credit note with KRA and cannot be undone.
	"""
	from erpnext.accounts.doctype.sales_invoice.sales_invoice import make_sales_return
	from tims_integration.services.rest import send_request

	original = frappe.get_doc("Sales Invoice", sales_invoice)
	if not original.custom_sent_to_kra:
		frappe.throw("The original invoice has not been successfully sent to KRA yet.")

	credit_note = frappe.get_doc(make_sales_return(sales_invoice))
	credit_note.insert(ignore_permissions=True)
	credit_note.submit()
	frappe.db.commit()

	send_request(credit_note.name)

	credit_note = frappe.get_doc("Sales Invoice", credit_note.name)
	return {
		"credit_note": credit_note.name,
		"sent_to_kra": credit_note.custom_sent_to_kra,
		"response_code": credit_note.custom_tims_code,
	}


def _ensure_test_item():
	item_code = "TIMS-TEST-ITEM"
	if not frappe.db.exists("Item", item_code):
		item_group = frappe.db.get_value("Item Group", {"is_group": 0}) or "All Item Groups"
		frappe.get_doc({
			"doctype": "Item",
			"item_code": item_code,
			"item_name": "TIMS Connectivity Test Item",
			"item_group": item_group,
			"stock_uom": "Nos",
			"is_stock_item": 0,
			"standard_rate": 1,
		}).insert(ignore_permissions=True)
	return item_code


def _ensure_test_customer():
	if frappe.db.exists("Customer", "Walk-in Customer"):
		return "Walk-in Customer"

	existing = frappe.db.get_value("Customer", {}, "name")
	if existing:
		return existing

	customer_group = frappe.db.get_value("Customer Group", {"is_group": 0})
	territory = frappe.db.get_value("Territory", {"is_group": 0})
	customer = frappe.get_doc({
		"doctype": "Customer",
		"customer_name": "TIMS Test Customer",
		"customer_group": customer_group,
		"territory": territory,
	})
	customer.insert(ignore_permissions=True)
	return customer.name
