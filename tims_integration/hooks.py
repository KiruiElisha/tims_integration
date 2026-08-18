app_name = "tims_integration"
app_title = "TIMS Integration"
app_publisher = "RONOH"
app_description = "KRA TIMS Integration for erpnext"
app_email = "ronoelisha625@gmail.com"
app_license = "mit"
# required_apps = []

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/tims_integration/css/tims_integration.css"
# app_include_js = "/assets/tims_integration/js/tims_integration.js"

# include js, css files in header of web template
# web_include_css = "/assets/tims_integration/css/tims_integration.css"
# web_include_js = "/assets/tims_integration/js/tims_integration.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "tims_integration/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "tims_integration/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "tims_integration.utils.jinja_methods",
# 	"filters": "tims_integration.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "tims_integration.install.before_install"
# after_install = "tims_integration.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "tims_integration.uninstall.before_uninstall"
# after_uninstall = "tims_integration.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "tims_integration.utils.before_app_install"
# after_app_install = "tims_integration.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "tims_integration.utils.before_app_uninstall"
# after_app_uninstall = "tims_integration.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "tims_integration.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
# 	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------
# Hook on document methods and events

doc_events = {
    "Sales Invoice": {
        "on_submit": "tims_integration.api.sales_invoice_on_submit"
    }
}

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"tims_integration.tasks.all"
# 		],
# 	"daily": [
# 		"tims_integration.tasks.daily"
# 		],
# 	"hourly": [
# 		"tims_integration.tasks.hourly"
# 		],
# 	"weekly": [
# 		"tims_integration.tasks.weekly"
# 		],
# 	"monthly": [
# 		"tims_integration.tasks.monthly"
# 		],
# }

# Testing
# -------

# before_tests = "tims_integration.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "tims_integration.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "tims_integration.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["tims_integration.utils.before_request"]
# after_request = ["tims_integration.utils.after_request"]

# Job Events
# ----------
# before_job = ["tims_integration.utils.before_job"]
# after_job = ["tims_integration.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"tims_integration.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

fixtures = [
    {
        "dt": "Custom Field",
        "filters": [
            ["name", "like", "Sales Invoice-custom_%"]
        ]
    },
    {
        "dt": "Property Setter",
        "filters": [
            ["doc_type", "=", "Sales Invoice"]
        ]
    },
    # KRA Response and TIMS Device Setup are this app's own DocTypes: they live in
    # tims_integration/tims_integration/doctype/ and are created by bench migrate.
    # Exporting them as fixtures too makes export-fixtures write a doctype.json that
    # then competes with those module definitions on every migrate.
]

# sales_invoice.js is a Sales Invoice form script, so it belongs in doctype_js only.
# It was also listed in app_include_js, which loads it on every desk page and
# registers the refresh handler a second time.
doctype_js = {
    "Sales Invoice": "public/js/sales_invoice.js"
}

