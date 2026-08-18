frappe.ui.form.on('Sales Invoice', {
    refresh: function(frm) {
        // Only show button if invoice is submitted and not already sent to KRA
        if (frm.doc.docstatus === 1 && !frm.doc.custom_sent_to_kra) {
            frm.add_custom_button(__('Send to TIMS'), function() {
                send_to_tims(frm);
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