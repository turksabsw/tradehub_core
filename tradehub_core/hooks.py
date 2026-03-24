app_name = "tradehub_core"
app_title = "TradeHub Core"
app_publisher = "TradeHub"
app_description = "TradeHub B2B E-Commerce Backend"
app_email = "dev@tradehub.com"
app_license = "MIT"

after_install = "tradehub_core.setup.install.after_install"
after_migrate = "tradehub_core.setup.install.after_install"
app_icon = "octicon octicon-organization"
app_color = "#0066CC"

required_apps = []

app_include_js = "seller_redirect.js"

fixtures = [
	{
		"dt": "Role",
		"filters": [["name", "in", ["Marketplace Seller", "Buyer", "Seller", "Marketplace Admin", "Marketplace Buyer"]]],
	},
	{
		"dt": "Workspace",
		"filters": [["module", "=", "Tradehub Core"]],
	},
]

scheduler_events = {
	"daily": [
		"tradehub_core.setup.install.cleanup_expired_tokens",
	],
}
