"""
Regenerates fixtures/print_format.json from tims_tax_invoice.html.

The HTML is edited as a file rather than as an escaped string inside JSON, so the
fixture is generated from it. Run this after changing the template:

    python3 tims_integration/print_format/build_fixture.py
"""

import io
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)

SOURCE = os.path.join(HERE, "tims_tax_invoice.html")
FIXTURE = os.path.join(APP, "fixtures", "print_format.json")

PRINT_FORMAT_NAME = "Tax Invoice KRA"


def build():
    html = io.open(SOURCE, encoding="utf-8").read()

    record = {
        "absolute_value": 0,
        "align_labels_right": 0,
        "custom_format": 1,
        "default_print_language": "en",
        "disabled": 0,
        "doc_type": "Sales Invoice",
        "docstatus": 0,
        "doctype": "Print Format",
        "font_size": 0,
        "html": html,
        "line_breaks": 0,
        "margin_bottom": 10.0,
        "margin_left": 10.0,
        "margin_right": 10.0,
        "margin_top": 10.0,
        "module": "TIMS Integration",
        "name": PRINT_FORMAT_NAME,
        "page_number": "Hide",
        "print_format_builder": 0,
        "print_format_type": "Jinja",
        "raw_printing": 0,
        "show_section_headings": 0,
        "standard": "No",
    }

    io.open(FIXTURE, "w", encoding="utf-8").write(
        json.dumps([record], indent=1) + "\n"
    )
    return len(html)


if __name__ == "__main__":
    print("wrote {0} ({1} chars of HTML)".format(FIXTURE, build()))
