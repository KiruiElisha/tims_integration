frappe.ui.form.on('Sales Invoice', {
    refresh: function(frm) {
        // Only show button if invoice is submitted and not already sent to KRA
        if (frm.doc.docstatus === 1 && !frm.doc.custom_sent_to_kra) {
            frm.add_custom_button(__('Send to TIMS'), function() {
                send_to_tims(frm);
            }, __('TIMS'));
        }

        // A price adjustment only makes sense against an invoice TIMS already
        // has an accepted sale for - that accepted payload is what the
        // allowance (and the re-encoding on send) is read from.
        if (frm.doc.docstatus === 1 && !frm.doc.is_return && frm.doc.custom_sent_to_kra) {
            frm.add_custom_button(__('Price Adjustment'), function() {
                open_price_adjustment_dialog(frm);
            }, __('TIMS'));
        }

        // Show TIMS status in the dashboard
        if (frm.doc.custom_sent_to_kra) {
            // KRA returns a zero-padded code, but the field held an Int on older
            // installs, so '000' can have been stored as 0. Compare numerically.
            let code = parseInt(frm.doc.custom_tims_response_code, 10);
            let succeeded = code === 0;
            let status_color = succeeded ? 'green' : 'red';
            let status_message = succeeded ?
                'Successfully sent to TIMS' :
                'Failed to send to TIMS';

            frm.dashboard.add_indicator(
                __(`TIMS Status: ${status_message}`),
                status_color
            );

            // Show TIMS details section
            show_tims_details(frm);
        }
    }
});

function send_to_tims(frm) {
    // Never submit straight to KRA: fiscalisation cannot be undone, so show the
    // figures that would be declared and let the user confirm them first.
    frappe.call({
        method: 'tims_integration.services.rest.preview_submission',
        args: { invoice: frm.doc.name },
        freeze: true,
        freeze_message: __('Preparing TIMS submission...'),
        callback: function(r) {
            if (!r.message) return;
            show_confirmation_dialog(frm, r.message);
        }
    });
}

function money(value) {
    return format_number(value || 0, null, 2);
}

function build_confirmation_html(preview) {
    const p = preview.payload;
    let html = '';

    if (preview.unclassified && preview.unclassified.length) {
        let rows = preview.unclassified.map(u => `
            <tr>
                <td>${frappe.utils.escape_html(u.item_code || '')}</td>
                <td class="text-right">${money(u.qty)}</td>
                <td class="text-right">${money(u.net_amount)}</td>
            </tr>`).join('');
        html += `
            <div class="alert alert-warning" style="margin-bottom:12px">
                <b>${__('Tax not set on this invoice')}</b>
                <div style="margin-top:4px">
                    ${__('No tax template or invoice tax row could be resolved for the items below. They will be declared to KRA at {0}% VAT. Confirm this is correct, or cancel and set the tax template on the item.', [preview.assumed_rate])}
                </div>
                <table class="table table-bordered" style="margin:8px 0 0">
                    <thead><tr>
                        <th>${__('Item')}</th>
                        <th class="text-right">${__('Qty')}</th>
                        <th class="text-right">${__('Net Amount')}</th>
                    </tr></thead>
                    <tbody>${rows}</tbody>
                </table>
            </div>`;
    }

    // Anything else the server flagged. Rendered generically so a newly added
    // concern still reaches the user even if it has no bespoke block here.
    let other = (preview.concerns || []).filter(c => !c.startsWith('No tax template'));
    if (other.length) {
        html += `
            <div class="alert alert-warning" style="margin-bottom:12px">
                <b>${__('Check before confirming')}</b>
                <ul style="margin:4px 0 0 -18px">
                    ${other.map(c => `<li>${frappe.utils.escape_html(c)}</li>`).join('')}
                </ul>
            </div>`;
    }

    let bands = ['A', 'B', 'C', 'D', 'E', 'F']
        .filter(b => p['VAT_' + b + '_Net'] || p['VAT_' + b])
        .map(b => `
            <tr>
                <td>${__('Band')} ${b}</td>
                <td class="text-right">${money(p['VAT_' + b + '_Net'])}</td>
                <td class="text-right">${money(p['VAT_' + b])}</td>
            </tr>`).join('');

    let lines = (p.data || []).map(d => `
        <tr>
            <td>${frappe.utils.escape_html(d.productDesc || d.productCode || '')}</td>
            <td class="text-right">${money(d.quantity)}</td>
            <td class="text-right">${money(d.unitPrice)}</td>
            <td class="text-right">${frappe.utils.escape_html(String(d.taxtype))}</td>
        </tr>`).join('');

    html += `
        <p>${__('The following will be declared to KRA. This cannot be undone once sent.')}</p>
        <table class="table table-bordered">
            <tbody>
                <tr><td>${__('Type')}</td><td class="text-right">${frappe.utils.escape_html(p.saleType)}</td></tr>
                <tr><td>${__('Receipt No')}</td><td class="text-right">${frappe.utils.escape_html(p.rctNo)}</td></tr>
                ${p.cuin ? `<tr><td>${__('Original CUIN')}</td><td class="text-right">${frappe.utils.escape_html(p.cuin)}</td></tr>` : ''}
                <tr><td><b>${__('Total')}</b></td><td class="text-right"><b>${money(p.total)}</b></td></tr>
                <tr><td>${__('Invoice Grand Total')}</td><td class="text-right">${money(preview.invoice_totals.grand_total)}</td></tr>
            </tbody>
        </table>

        <table class="table table-bordered">
            <thead><tr>
                <th>${__('VAT Band')}</th>
                <th class="text-right">${__('Taxable Amount')}</th>
                <th class="text-right">${__('VAT')}</th>
            </tr></thead>
            <tbody>${bands || `<tr><td colspan="3">${__('No VAT reported')}</td></tr>`}</tbody>
        </table>

        <table class="table table-bordered">
            <thead><tr>
                <th>${__('Item')}</th>
                <th class="text-right">${__('Qty')}</th>
                <th class="text-right">${__('Unit Price (incl. VAT)')}</th>
                <th class="text-right">${__('Tax Type')}</th>
            </tr></thead>
            <tbody>${lines}</tbody>
        </table>`;

    return html;
}

function show_confirmation_dialog(frm, preview) {
    const d = new frappe.ui.Dialog({
        title: __('Confirm TIMS Submission'),
        size: 'large',
        fields: [{ fieldtype: 'HTML', fieldname: 'summary' }],
        primary_action_label: __('Confirm and Send'),
        primary_action() {
            d.hide();
            frappe.call({
                method: 'tims_integration.services.rest.send_request',
                args: { invoice: frm.doc.name, confirmed: 1 },
                freeze: true,
                freeze_message: __('Sending to TIMS...'),
                callback: function() {
                    frm.reload_doc();
                }
            });
        },
        secondary_action_label: __('Cancel'),
        secondary_action() {
            d.hide();
        }
    });

    d.fields_dict.summary.$wrapper.html(build_confirmation_html(preview));
    d.show();
}

function open_price_adjustment_dialog(frm) {
    frappe.call({
        method: 'tims_integration.services.allowance.get_allowance',
        args: { invoice: frm.doc.name },
        freeze: true,
        freeze_message: __('Checking TIMS allowance...'),
        callback: function(r) {
            const data = r.message;
            if (!data || !data.available) {
                frappe.msgprint({
                    title: __('No TIMS Allowance'),
                    message: (data && data.message) || __('This invoice has no accepted TIMS sale recorded, so nothing can be credited against it yet.'),
                    indicator: 'orange'
                });
                return;
            }
            show_price_adjustment_dialog(frm, data);
        }
    });
}

function show_price_adjustment_dialog(frm, allowance) {
    // Lines are keyed by description on the TIMS side; map back to the item
    // code the invoice actually uses so the credit note can be built from it.
    const item_code_by_desc = {};
    (frm.doc.items || []).forEach(it => {
        item_code_by_desc[it.description || it.item_name || it.item_code] = it.item_code;
    });

    const creditable = (allowance.lines || []).filter(line => flt(line.remaining_amount) > 0);

    if (!creditable.length) {
        frappe.msgprint(__('No item on this invoice has any TIMS credit remaining.'));
        return;
    }

    const fields = [{ fieldtype: 'HTML', fieldname: 'allowance_summary' }];
    creditable.forEach((line, i) => {
        fields.push({
            fieldtype: 'Currency',
            fieldname: 'amount_' + i,
            label: __('{0} - Amount to Refund', [line.description]),
            description: __('Originally invoiced at {0} ({1} @ {2}). Up to {3} of that is still available to credit.', [
                money(line.original_amount), line.original_qty, money(line.unit_price), money(line.remaining_amount)
            ]),
            default: 0
        });
    });

    const d = new frappe.ui.Dialog({
        title: __('TIMS Price Adjustment'),
        size: 'large',
        fields: fields,
        primary_action_label: __('Create and Preview'),
        primary_action(values) {
            const items = [];
            creditable.forEach((line, i) => {
                const amt = flt(values['amount_' + i]);
                if (amt > 0) {
                    items.push({
                        item_code: item_code_by_desc[line.description] || line.description,
                        amount: amt
                    });
                }
            });
            if (!items.length) {
                frappe.msgprint(__('Enter an amount to credit for at least one item.'));
                return;
            }
            d.hide();
            create_and_preview_price_adjustment(frm, items, allowance);
        },
        secondary_action_label: __('Cancel'),
        secondary_action() {
            d.hide();
        }
    });

    d.fields_dict.allowance_summary.$wrapper.html(`
        <div class="alert alert-info">
            <b>${__('This is a price concession, not a return.')}</b>
            <div style="margin-top:4px">
                ${__('Enter how much money you are giving back to the customer for each item - not the new price. For example, if you agreed to knock KES 200 off an item, enter 200, not the reduced price. No goods or stock are affected.')}
            </div>
            <div style="margin-top:8px">
                ${__('So far on {0}: {1} originally invoiced, {2} already credited, {3} still available to credit.', [
                    allowance.invoice, money(allowance.original_amount), money(allowance.credited_amount), money(allowance.remaining_amount)
                ])}
            </div>
        </div>
    `);

    d.show();
}

function create_and_preview_price_adjustment(frm, items, allowance) {
    // create_price_adjustment only builds a draft - nothing is submitted or sent
    // yet, so the preview below always reflects a document KRA has not seen.
    frappe.call({
        method: 'tims_integration.services.rest.create_price_adjustment',
        args: { original_invoice: frm.doc.name, items: items },
        freeze: true,
        freeze_message: __('Preparing credit note...'),
        callback: function(r) {
            const credit_note = r.message;
            if (!credit_note) return;

            frappe.call({
                method: 'tims_integration.services.rest.preview_submission',
                args: { invoice: credit_note },
                freeze: true,
                freeze_message: __('Preparing TIMS submission...'),
                callback: function(r2) {
                    if (!r2.message) return;
                    show_price_adjustment_confirmation(frm, credit_note, r2.message, items, allowance);
                }
            });
        }
    });
}

function build_price_adjustment_summary_html(items, allowance) {
    const total = items.reduce((sum, it) => sum + flt(it.amount), 0);
    const rows = items.map(it => `
        <tr>
            <td>${frappe.utils.escape_html(it.item_code)}</td>
            <td class="text-right">${money(it.amount)}</td>
        </tr>`).join('');
    const remaining_after = flt(allowance.remaining_amount) - total;

    return `
        <div class="alert alert-warning" style="margin-bottom:12px">
            <b>${__('Price Adjustment Summary')}</b>
            <div style="margin:4px 0 8px">
                ${__('This refunds/adjusts {0} against {1} - no goods are returned, only the price is reduced.', [
                    `<b>${money(total)}</b>`, allowance.invoice
                ])}
            </div>
            <table class="table table-bordered" style="margin:0">
                <thead><tr>
                    <th>${__('Item')}</th>
                    <th class="text-right">${__('Amount Being Credited')}</th>
                </tr></thead>
                <tbody>${rows}</tbody>
                <tfoot><tr>
                    <td><b>${__('Total Credited in This Adjustment')}</b></td>
                    <td class="text-right"><b>${money(total)}</b></td>
                </tr></tfoot>
            </table>
            <div style="margin-top:8px">
                ${__('{0} was originally invoiced for {1}; {2} has been credited so far. After this adjustment, {3} will be left to credit.', [
                    allowance.invoice, money(allowance.original_amount), money(allowance.credited_amount), money(remaining_after)
                ])}
            </div>
        </div>`;
}

function show_price_adjustment_confirmation(frm, credit_note, preview, items, allowance) {
    const d = new frappe.ui.Dialog({
        title: __('Confirm Price Adjustment - {0}', [credit_note]),
        size: 'large',
        fields: [{ fieldtype: 'HTML', fieldname: 'summary' }],
        primary_action_label: __('Submit and Send to TIMS'),
        primary_action() {
            d.hide();
            frappe.call({
                method: 'tims_integration.services.rest.submit_and_send_price_adjustment',
                args: { invoice: credit_note },
                freeze: true,
                freeze_message: __('Submitting and sending to TIMS...'),
                callback: function() {
                    frappe.show_alert({
                        message: __('Price adjustment {0} sent to TIMS.', [credit_note]),
                        indicator: 'green'
                    });
                    frappe.set_route('Form', 'Sales Invoice', credit_note);
                }
            });
        },
        secondary_action_label: __('Cancel'),
        secondary_action() {
            d.hide();
            // Nothing was submitted or sent - the credit note exists only as a
            // draft, so leave the user somewhere they can review, edit or
            // delete it rather than silently discarding the work.
            frappe.msgprint({
                title: __('Draft Not Submitted'),
                message: __('{0} was created as a draft but not submitted or sent to TIMS. You can open it, edit the amounts, and use the TIMS button there when ready.', [credit_note]),
                indicator: 'blue',
                primary_action: {
                    label: __('Open Draft'),
                    action: function() {
                        frappe.set_route('Form', 'Sales Invoice', credit_note);
                    }
                }
            });
        }
    });

    d.fields_dict.summary.$wrapper.html(
        build_price_adjustment_summary_html(items, allowance) + build_confirmation_html(preview)
    );
    d.show();
}

function show_tims_details(frm) {
    if (frm.doc.custom_sent_to_kra) {
        let html = `
            <div class="tims-details" style="padding: 10px; margin-top: 10px;">
                <div class="row">
                    <div class="col-sm-6">
                        <strong>TIMS Response Code:</strong> ${frm.doc.custom_tims_response_code || ''}
                    </div>
                    <div class="col-sm-6">
                        <strong>Signing Time:</strong> ${frm.doc.custom_kra_signing_time || ''}
                    </div>
                </div>
                <div class="row" style="margin-top: 10px;">
                    <div class="col-sm-4">
                        <strong>TSIN:</strong> ${frm.doc.custom_tsin || ''}
                    </div>
                    <div class="col-sm-4">
                        <strong>CUSN:</strong> ${frm.doc.custom_cusn || ''}
                    </div>
                    <div class="col-sm-4">
                        <strong>CUIN:</strong> ${frm.doc.custom_cuin || ''}
                    </div>
                </div>
                ${frm.doc.custom_kra_qr_code_data ? `
                <div class="row" style="margin-top: 10px;">
                    <div class="col-sm-12">
                        <strong>QR Code Data:</strong>
                        <div style="word-break: break-all; margin-top: 5px;">
                            ${frm.doc.custom_kra_qr_code_data}
                        </div>
                    </div>
                </div>
                ` : ''}
            </div>
        `;

        $(frm.dashboard.wrapper).find('.tims-details').remove();
        $(frm.dashboard.wrapper).append(html);
    }
} 