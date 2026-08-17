// Copyright (c) 2024, RONOH and contributors
// For license information, please see license.txt

 frappe.ui.form.on("TIMS Device Setup", {
    refresh(frm) {
        // Show current connection status on dashboard
        if (frm.doc.status === "Active") {
            frm.dashboard.set_headline_alert(
                `<div class="row">
                    <div class="col"><span class="indicator green">Connected</span></div>
                </div>`
            );
        } else {
            frm.dashboard.set_headline_alert(
                `<div class="row">
                    <div class="col"><span class="indicator red">Disconnected</span></div>
                </div>`
            );
        }

        frm.add_custom_button(__('Test Connection'), function() {
            frm.disable_save();
            frappe.call({
                method: "tims_integration.tims_integration.doctype.tims_device_setup.tims_device_setup.test_connection",
                args: {
                    ip: frm.doc.ip,
                    port: frm.doc.port,
                    name: frm.doc.name
                },
                freeze: true,
                freeze_message: __('Testing Connection...'),
                callback: function(r) {
                    frm.enable_save();
                    if (r.message && r.message.success) {
                        frm.dashboard.set_headline_alert(
                            `<div class="row">
                                <div class="col"><span class="indicator green">Connected</span></div>
                            </div>`
                        );
                        frappe.show_alert({
                            message: __('Connection Successful'),
                            indicator: 'green'
                        }, 5);
                        frm.reload_doc();
                    } else {
                        frm.dashboard.set_headline_alert(
                            `<div class="row">
                                <div class="col"><span class="indicator red">Disconnected</span></div>
                            </div>`
                        );
                        frappe.show_alert({
                            message: __('Connection Failed: ') + (r.message.error || 'Unknown Error'),
                            indicator: 'red'
                        }, 5);
                        frm.reload_doc();
                    }
                }
            });
        }, __('Actions'));

        frm.add_custom_button(__('Send Test Invoice (KES 1)'), function() {
            frappe.confirm(
                __('This creates and submits a real Sales Invoice for KES 1 and sends it to your live TIMS device / KRA. This action registers a genuine fiscal invoice and cannot be undone. Continue?'),
                function() {
                    frappe.call({
                        method: "tims_integration.tims_integration.doctype.tims_device_setup.tims_device_setup.send_test_invoice",
                        args: { name: frm.doc.name },
                        freeze: true,
                        freeze_message: __('Sending test invoice to TIMS...'),
                        callback: function(r) {
                            if (r.message) {
                                frm.last_test_invoice = r.message.invoice;
                                frappe.msgprint(
                                    __('Test invoice {0} created. Sent to KRA: {1}. Response code: {2}', [
                                        r.message.invoice,
                                        r.message.sent_to_kra ? __('Yes') : __('No'),
                                        r.message.response_code || __('none')
                                    ])
                                );
                            }
                        }
                    });
                }
            );
        }, __('Test to KRA'));

        frm.add_custom_button(__('Send Test Return'), function() {
            frappe.prompt(
                [{
                    fieldname: 'sales_invoice',
                    label: __('Test Sales Invoice'),
                    fieldtype: 'Link',
                    options: 'Sales Invoice',
                    reqd: 1,
                    default: frm.last_test_invoice,
                    description: __('Must be an invoice already sent to KRA, e.g. one created via "Send Test Invoice".')
                }],
                function(values) {
                    frappe.confirm(
                        __('This creates and submits a full credit note against {0} and sends it to your live TIMS device / KRA as a refund. This action registers a genuine fiscal credit note and cannot be undone. Continue?', [values.sales_invoice]),
                        function() {
                            frappe.call({
                                method: "tims_integration.tims_integration.doctype.tims_device_setup.tims_device_setup.send_test_return",
                                args: { sales_invoice: values.sales_invoice },
                                freeze: true,
                                freeze_message: __('Sending test return to TIMS...'),
                                callback: function(r) {
                                    if (r.message) {
                                        frappe.msgprint(
                                            __('Credit note {0} created. Sent to KRA: {1}. Response code: {2}', [
                                                r.message.credit_note,
                                                r.message.sent_to_kra ? __('Yes') : __('No'),
                                                r.message.response_code || __('none')
                                            ])
                                        );
                                    }
                                }
                            });
                        }
                    );
                },
                __('Send Test Return'),
                __('Send')
            );
        }, __('Test to KRA'));
    },
});

