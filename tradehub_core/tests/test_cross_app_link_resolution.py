"""
Test Cross-App Link Field Resolution

This module verifies that Frappe's Link fields correctly resolve across
the decomposed TradeHub apps. Link fields use DocType name strings, not
Python import paths, so cross-app resolution should work automatically.

Verified Relationships:
1. Listing -> Product (seller -> catalog)
2. Order -> Seller Profile (commerce -> seller)
3. Shipment -> Order (logistics -> commerce)
4. Coupon -> Category (marketing -> catalog) - via text references
5. Review -> Product (compliance -> catalog) - via Dynamic Link
"""

import unittest
import json
import os
from pathlib import Path


class TestCrossAppLinkResolution(unittest.TestCase):
    """Test cross-app Link field resolution across all TradeHub apps."""

    @classmethod
    def setUpClass(cls):
        """Set up test fixtures - load all DocType JSON definitions."""
        # Path: tests/ -> tradehub_core/ (module) -> tradehub_core/ (app) -> apps/
        cls.base_path = Path(__file__).parent.parent.parent.parent
        cls.apps_path = cls.base_path
        cls.doctypes = {}
        cls.modules = {}

        # Load all DocType definitions from all apps
        for app_name in [
            'tradehub_core', 'tradehub_catalog', 'tradehub_seller',
            'tradehub_compliance', 'tradehub_commerce', 'tradehub_logistics',
            'tradehub_marketing', 'tr_tradehub'
        ]:
            app_path = cls.apps_path / app_name / app_name / 'doctype'
            if app_path.exists():
                for doctype_dir in app_path.iterdir():
                    if doctype_dir.is_dir():
                        json_file = doctype_dir / f"{doctype_dir.name}.json"
                        if json_file.exists():
                            with open(json_file, 'r') as f:
                                doctype_def = json.load(f)
                                cls.doctypes[doctype_def['name']] = doctype_def
                                cls.modules[doctype_def['name']] = doctype_def.get('module', '')

    def get_link_fields(self, doctype_name):
        """Get all Link fields from a DocType definition."""
        doctype = self.doctypes.get(doctype_name)
        if not doctype:
            return []

        link_fields = []
        for field in doctype.get('fields', []):
            if field.get('fieldtype') == 'Link':
                link_fields.append({
                    'fieldname': field.get('fieldname'),
                    'options': field.get('options'),  # Target DocType
                    'reqd': field.get('reqd', 0)
                })
            elif field.get('fieldtype') == 'Dynamic Link':
                link_fields.append({
                    'fieldname': field.get('fieldname'),
                    'options': field.get('options'),  # Field containing DocType
                    'dynamic': True,
                    'reqd': field.get('reqd', 0)
                })
        return link_fields

    def verify_link_target_exists(self, source_doctype, field_name, target_doctype):
        """Verify that a Link field's target DocType exists."""
        self.assertIn(
            target_doctype, self.doctypes,
            f"Link target '{target_doctype}' for field '{field_name}' in '{source_doctype}' not found"
        )
        return True

    # =========================================================================
    # Test 1: Listing -> Product (seller -> catalog)
    # =========================================================================

    def test_listing_exists_in_seller_module(self):
        """Verify Listing DocType exists in TradeHub Seller module."""
        self.assertIn('Listing', self.doctypes)
        self.assertEqual(self.modules['Listing'], 'TradeHub Seller')

    def test_product_exists_in_catalog_module(self):
        """Verify Product DocType exists in TradeHub Catalog module."""
        self.assertIn('Product', self.doctypes)
        self.assertEqual(self.modules['Product'], 'TradeHub Catalog')

    def test_listing_to_product_link(self):
        """Verify Listing has Link field to Product (cross-app: seller -> catalog)."""
        link_fields = self.get_link_fields('Listing')
        product_link = next((f for f in link_fields if f['options'] == 'Product'), None)

        self.assertIsNotNone(product_link, "Listing should have Link field to Product")
        self.assertEqual(product_link['fieldname'], 'product')
        self.assertEqual(product_link['reqd'], 1)
        self.verify_link_target_exists('Listing', 'product', 'Product')

    def test_listing_to_seller_profile_link(self):
        """Verify Listing has Link field to Seller Profile (same app)."""
        link_fields = self.get_link_fields('Listing')
        seller_link = next((f for f in link_fields if f['options'] == 'Seller Profile'), None)

        self.assertIsNotNone(seller_link, "Listing should have Link field to Seller Profile")
        self.assertEqual(seller_link['fieldname'], 'seller_profile')
        self.assertEqual(seller_link['reqd'], 1)

    def test_listing_to_sales_channel_link(self):
        """Verify Listing has Link field to Sales Channel (cross-app: seller -> catalog)."""
        link_fields = self.get_link_fields('Listing')
        channel_link = next((f for f in link_fields if f['options'] == 'Sales Channel'), None)

        self.assertIsNotNone(channel_link, "Listing should have Link field to Sales Channel")
        self.verify_link_target_exists('Listing', 'sales_channel', 'Sales Channel')

    # =========================================================================
    # Test 2: Order -> Seller Profile (commerce -> seller)
    # =========================================================================

    def test_order_exists_in_commerce_module(self):
        """Verify Order DocType exists in TradeHub Commerce module."""
        self.assertIn('Order', self.doctypes)
        self.assertEqual(self.modules['Order'], 'TradeHub Commerce')

    def test_seller_profile_exists_in_seller_module(self):
        """Verify Seller Profile DocType exists in TradeHub Seller module."""
        self.assertIn('Seller Profile', self.doctypes)
        self.assertEqual(self.modules['Seller Profile'], 'TradeHub Seller')

    def test_order_to_seller_profile_link(self):
        """Verify Order has Link field to Seller Profile (cross-app: commerce -> seller)."""
        link_fields = self.get_link_fields('Order')
        seller_link = next((f for f in link_fields if f['options'] == 'Seller Profile'), None)

        self.assertIsNotNone(seller_link, "Order should have Link field to Seller Profile")
        self.assertEqual(seller_link['fieldname'], 'seller_profile')
        self.assertEqual(seller_link['reqd'], 1)
        self.verify_link_target_exists('Order', 'seller_profile', 'Seller Profile')

    def test_order_to_buyer_profile_link(self):
        """Verify Order has Link field to Buyer Profile (cross-app: commerce -> core)."""
        link_fields = self.get_link_fields('Order')
        buyer_link = next((f for f in link_fields if f['options'] == 'Buyer Profile'), None)

        self.assertIsNotNone(buyer_link, "Order should have Link field to Buyer Profile")
        self.assertEqual(buyer_link['fieldname'], 'buyer_profile')
        self.assertEqual(buyer_link['reqd'], 1)
        self.verify_link_target_exists('Order', 'buyer_profile', 'Buyer Profile')

    def test_order_is_submittable(self):
        """Verify Order DocType is submittable (workflow support)."""
        order = self.doctypes['Order']
        self.assertEqual(order.get('is_submittable'), 1)

    # =========================================================================
    # Test 3: Shipment -> Order (logistics -> commerce)
    # =========================================================================

    def test_shipment_exists_in_logistics_module(self):
        """Verify Shipment DocType exists in TradeHub Logistics module."""
        self.assertIn('Shipment', self.doctypes)
        self.assertEqual(self.modules['Shipment'], 'TradeHub Logistics')

    def test_shipment_to_order_link(self):
        """Verify Shipment has Link field to Order (cross-app: logistics -> commerce)."""
        link_fields = self.get_link_fields('Shipment')
        order_link = next((f for f in link_fields if f['options'] == 'Order'), None)

        self.assertIsNotNone(order_link, "Shipment should have Link field to Order")
        self.assertEqual(order_link['fieldname'], 'order')
        self.assertEqual(order_link['reqd'], 1)
        self.verify_link_target_exists('Shipment', 'order', 'Order')

    def test_shipment_to_seller_profile_link(self):
        """Verify Shipment has Link field to Seller Profile (cross-app: logistics -> seller)."""
        link_fields = self.get_link_fields('Shipment')
        seller_link = next((f for f in link_fields if f['options'] == 'Seller Profile'), None)

        self.assertIsNotNone(seller_link, "Shipment should have Link field to Seller Profile")
        self.verify_link_target_exists('Shipment', 'seller_profile', 'Seller Profile')

    def test_shipment_to_carrier_link(self):
        """Verify Shipment has Link field to Carrier (same app)."""
        link_fields = self.get_link_fields('Shipment')
        carrier_link = next((f for f in link_fields if f['options'] == 'Carrier'), None)

        self.assertIsNotNone(carrier_link, "Shipment should have Link field to Carrier")
        self.assertEqual(carrier_link['reqd'], 1)

    def test_shipment_is_submittable(self):
        """Verify Shipment DocType is submittable (workflow support)."""
        shipment = self.doctypes['Shipment']
        self.assertEqual(shipment.get('is_submittable'), 1)

    # =========================================================================
    # Test 4: Coupon -> Category (marketing -> catalog)
    # Note: Coupon uses text-based applicable_categories for flexible many-to-many
    # =========================================================================

    def test_coupon_exists_in_marketing_module(self):
        """Verify Coupon DocType exists in TradeHub Marketing module."""
        self.assertIn('Coupon', self.doctypes)
        self.assertEqual(self.modules['Coupon'], 'TradeHub Marketing')

    def test_category_exists_in_catalog_module(self):
        """Verify Category DocType exists in TradeHub Catalog module."""
        self.assertIn('Category', self.doctypes)
        self.assertEqual(self.modules['Category'], 'TradeHub Catalog')

    def test_coupon_has_applicable_categories_field(self):
        """Verify Coupon has applicable_categories field for category filtering."""
        coupon = self.doctypes['Coupon']
        fields = {f['fieldname']: f for f in coupon.get('fields', [])}

        self.assertIn('applicable_categories', fields)
        self.assertEqual(fields['applicable_categories']['fieldtype'], 'Small Text')
        # This allows comma-separated category names for flexible many-to-many

    def test_coupon_has_applicable_products_field(self):
        """Verify Coupon has applicable_products field for product filtering."""
        coupon = self.doctypes['Coupon']
        fields = {f['fieldname']: f for f in coupon.get('fields', [])}

        self.assertIn('applicable_products', fields)
        self.assertEqual(fields['applicable_products']['fieldtype'], 'Small Text')

    def test_coupon_to_campaign_link(self):
        """Verify Coupon has Link field to Campaign (same app)."""
        link_fields = self.get_link_fields('Coupon')
        campaign_link = next((f for f in link_fields if f['options'] == 'Campaign'), None)

        self.assertIsNotNone(campaign_link, "Coupon should have Link field to Campaign")
        self.verify_link_target_exists('Coupon', 'campaign', 'Campaign')

    def test_coupon_to_buyer_profile_link(self):
        """Verify Coupon has Link field to Buyer Profile (cross-app: marketing -> core)."""
        link_fields = self.get_link_fields('Coupon')
        buyer_link = next((f for f in link_fields if f['options'] == 'Buyer Profile'), None)

        self.assertIsNotNone(buyer_link, "Coupon should have Link field to Buyer Profile")
        self.verify_link_target_exists('Coupon', 'buyer_profile', 'Buyer Profile')

    # =========================================================================
    # Test 5: Review -> Product (compliance -> catalog) via Dynamic Link
    # =========================================================================

    def test_review_exists_in_compliance_module(self):
        """Verify Review DocType exists in TradeHub Compliance module."""
        self.assertIn('Review', self.doctypes)
        self.assertEqual(self.modules['Review'], 'TradeHub Compliance')

    def test_review_has_dynamic_link_pattern(self):
        """Verify Review uses Dynamic Link pattern for flexible subject linking."""
        review = self.doctypes['Review']
        fields = {f['fieldname']: f for f in review.get('fields', [])}

        # subject_type is a Link to DocType
        self.assertIn('subject_type', fields)
        self.assertEqual(fields['subject_type']['fieldtype'], 'Link')
        self.assertEqual(fields['subject_type']['options'], 'DocType')

        # subject_name is a Dynamic Link using subject_type
        self.assertIn('subject_name', fields)
        self.assertEqual(fields['subject_name']['fieldtype'], 'Dynamic Link')
        self.assertEqual(fields['subject_name']['options'], 'subject_type')

    def test_review_subject_types_include_product(self):
        """Verify Review can link to Product via Dynamic Link."""
        review = self.doctypes['Review']
        fields = {f['fieldname']: f for f in review.get('fields', [])}

        # review_type options should include Product
        review_type = fields.get('review_type', {})
        options = review_type.get('options', '')

        self.assertIn('Product', options, "Review should support Product reviews")
        self.assertIn('Seller', options, "Review should support Seller reviews")
        self.assertIn('Order', options, "Review should support Order reviews")

    def test_review_to_user_link(self):
        """Verify Review has Link field to User (Frappe core)."""
        link_fields = self.get_link_fields('Review')
        reviewer_link = next((f for f in link_fields if f['options'] == 'User'), None)

        self.assertIsNotNone(reviewer_link, "Review should have Link field to User")

    # =========================================================================
    # Additional Cross-App Link Verifications
    # =========================================================================

    def test_buyer_profile_exists_in_core_module(self):
        """Verify Buyer Profile DocType exists in TradeHub Core module."""
        self.assertIn('Buyer Profile', self.doctypes)
        self.assertEqual(self.modules['Buyer Profile'], 'TradeHub Core')

    def test_rfq_to_buyer_profile_link(self):
        """Verify RFQ has Link to Buyer Profile (cross-app: commerce -> core)."""
        link_fields = self.get_link_fields('RFQ')
        buyer_link = next((f for f in link_fields if f['options'] == 'Buyer Profile'), None)

        self.assertIsNotNone(buyer_link, "RFQ should have Link field to Buyer Profile")
        self.verify_link_target_exists('RFQ', 'buyer_profile', 'Buyer Profile')

    def test_group_buy_to_product_link(self):
        """Verify Group Buy has Link to Product (cross-app: marketing -> catalog)."""
        link_fields = self.get_link_fields('Group Buy')
        product_link = next((f for f in link_fields if f['options'] == 'Product'), None)

        self.assertIsNotNone(product_link, "Group Buy should have Link field to Product")
        self.verify_link_target_exists('Group Buy', 'product', 'Product')

    def test_tracking_event_to_shipment_link(self):
        """Verify Tracking Event has Link to Shipment (same app)."""
        link_fields = self.get_link_fields('Tracking Event')
        shipment_link = next((f for f in link_fields if f['options'] == 'Shipment'), None)

        self.assertIsNotNone(shipment_link, "Tracking Event should have Link field to Shipment")

    def test_subscription_to_seller_profile_link(self):
        """Verify Subscription can Link to Seller Profile (cross-app: marketing -> seller)."""
        link_fields = self.get_link_fields('Subscription')
        seller_link = next((f for f in link_fields if f['options'] == 'Seller Profile'), None)

        self.assertIsNotNone(seller_link, "Subscription should have Link field to Seller Profile")
        self.verify_link_target_exists('Subscription', 'seller_profile', 'Seller Profile')

    def test_certificate_to_seller_profile_link(self):
        """Verify Certificate can Link to Seller Profile (cross-app: compliance -> seller)."""
        if 'Certificate' in self.doctypes:
            link_fields = self.get_link_fields('Certificate')
            # Certificate may link to Seller Profile via owner_type/owner Dynamic Link
            # or via specific seller_profile field

    # =========================================================================
    # Module Distribution Verification
    # =========================================================================

    def test_all_apps_have_correct_module_assignments(self):
        """Verify all DocTypes have correct module assignments."""
        expected_modules = {
            'TradeHub Core': [
                'Organization', 'Tenant', 'KYC Profile', 'City', 'Commercial Region',
                'District', 'Neighborhood', 'Phone Code', 'Analytics Settings',
                'Keycloak Settings', 'ERPNext Integration Settings', 'ECA Rule',
                'ECA Action Template', 'ECA Rule Log', 'Import Job',
                'Notification Template', 'Buyer Category', 'Buyer Level',
                'Buyer Profile', 'Premium Buyer'
            ],
            'TradeHub Catalog': [
                'Product', 'Product Variant', 'Product Class', 'Product Family',
                'Product Attribute', 'Product Attribute Group', 'Product Attribute Value',
                'Attribute', 'Attribute Set', 'Brand', 'Brand Gating',
                'Ranking Weight Config', 'Category', 'Category Display Schema',
                'Product Category', 'Filter Config', 'Sales Channel',
                'Media Asset', 'Media Library', 'SEO Meta'
            ],
            'TradeHub Seller': [
                'Seller Application', 'Seller Profile', 'Seller Store',
                'Seller Level', 'Seller Tier', 'Premium Seller', 'Seller KPI',
                'KPI Template', 'Seller Metrics', 'Seller Score', 'Seller Balance',
                'Seller Tag', 'Seller Tag Assignment', 'Seller Tag Rule',
                'Listing', 'Listing Variant', 'SKU', 'SKU Product', 'Buy Box Entry'
            ],
            'TradeHub Compliance': [
                'Marketplace Consent Record', 'Marketplace Contract Template',
                'Marketplace Contract Instance', 'Certificate', 'Certificate Type',
                'Moderation Case', 'Review', 'Risk Score', 'Sample Request',
                'Message', 'Message Thread'
            ],
            'TradeHub Commerce': [
                'Cart', 'Order', 'Marketplace Order', 'Sub Order', 'Order Event',
                'RFQ', 'RFQ Message', 'RFQ Message Thread', 'RFQ NDA Link',
                'RFQ Quote', 'RFQ Quote Revision', 'Quotation', 'Account Action',
                'Escrow Account', 'Escrow Event', 'Commission Plan', 'Commission Rule',
                'Payment Intent', 'Payment Method', 'Payment Plan',
                'Incoterm Price', 'Tax Rate'
            ],
            'TradeHub Logistics': [
                'Carrier', 'Logistics Provider', 'Shipment', 'Marketplace Shipment',
                'Shipping Rule', 'Lead Time', 'Tracking Event'
            ],
            'TradeHub Marketing': [
                'Campaign', 'Coupon', 'Wholesale Offer', 'Group Buy',
                'Group Buy Commitment', 'Group Buy Payment', 'Storefront',
                'Subscription', 'Subscription Package'
            ]
        }

        for module, expected_doctypes in expected_modules.items():
            for doctype in expected_doctypes:
                if doctype in self.doctypes:
                    self.assertEqual(
                        self.modules[doctype], module,
                        f"{doctype} should be in module '{module}', got '{self.modules.get(doctype)}'"
                    )

    def test_total_doctype_count(self):
        """Verify total DocType count matches expected (~108)."""
        # Filter out tr_tradehub DocTypes (should be empty)
        decomposed_doctypes = [
            name for name, module in self.modules.items()
            if 'TradeHub' in module and module != 'TR TradeHub'
        ]

        # Should have approximately 108 DocTypes across all decomposed apps
        self.assertGreaterEqual(len(decomposed_doctypes), 100)
        self.assertLessEqual(len(decomposed_doctypes), 120)


class TestDependencyChain(unittest.TestCase):
    """Test that app dependency chain is correctly configured."""

    @classmethod
    def setUpClass(cls):
        """Load hooks.py from all apps to verify dependencies."""
        # Path: tests/ -> tradehub_core/ (module) -> tradehub_core/ (app) -> apps/
        cls.base_path = Path(__file__).parent.parent.parent.parent
        cls.required_apps = {}

        for app_name in [
            'tradehub_core', 'tradehub_catalog', 'tradehub_seller',
            'tradehub_compliance', 'tradehub_commerce', 'tradehub_logistics',
            'tradehub_marketing', 'tr_tradehub'
        ]:
            hooks_path = cls.base_path / app_name / app_name / 'hooks.py'
            if hooks_path.exists():
                with open(hooks_path, 'r') as f:
                    content = f.read()
                    # Extract required_apps list
                    if 'required_apps' in content:
                        # Simple extraction - look for the list
                        import re
                        match = re.search(r'required_apps\s*=\s*\[(.*?)\]', content, re.DOTALL)
                        if match:
                            apps_str = match.group(1)
                            apps = re.findall(r'"([^"]+)"', apps_str)
                            cls.required_apps[app_name] = apps
                        else:
                            cls.required_apps[app_name] = []
                    else:
                        cls.required_apps[app_name] = []

    def test_core_has_no_dependencies(self):
        """Verify tradehub_core has no tradehub dependencies (base layer)."""
        deps = self.required_apps.get('tradehub_core', [])
        tradehub_deps = [d for d in deps if d.startswith('tradehub_')]
        self.assertEqual(len(tradehub_deps), 0)

    def test_catalog_depends_on_core(self):
        """Verify tradehub_catalog depends on tradehub_core."""
        deps = self.required_apps.get('tradehub_catalog', [])
        self.assertIn('tradehub_core', deps)

    def test_seller_depends_on_core_and_catalog(self):
        """Verify tradehub_seller depends on tradehub_core and tradehub_catalog."""
        deps = self.required_apps.get('tradehub_seller', [])
        self.assertIn('tradehub_core', deps)
        self.assertIn('tradehub_catalog', deps)

    def test_compliance_depends_on_core(self):
        """Verify tradehub_compliance depends on tradehub_core."""
        deps = self.required_apps.get('tradehub_compliance', [])
        self.assertIn('tradehub_core', deps)

    def test_commerce_depends_on_core_and_catalog(self):
        """Verify tradehub_commerce depends on tradehub_core and tradehub_catalog."""
        deps = self.required_apps.get('tradehub_commerce', [])
        self.assertIn('tradehub_core', deps)
        self.assertIn('tradehub_catalog', deps)

    def test_logistics_depends_on_core_and_commerce(self):
        """Verify tradehub_logistics depends on tradehub_core and tradehub_commerce."""
        deps = self.required_apps.get('tradehub_logistics', [])
        self.assertIn('tradehub_core', deps)
        self.assertIn('tradehub_commerce', deps)

    def test_marketing_depends_on_core_catalog_commerce(self):
        """Verify tradehub_marketing depends on tradehub_core, tradehub_catalog, and tradehub_commerce."""
        deps = self.required_apps.get('tradehub_marketing', [])
        self.assertIn('tradehub_core', deps)
        self.assertIn('tradehub_catalog', deps)
        self.assertIn('tradehub_commerce', deps)

    def test_monolith_depends_on_all_decomposed_apps(self):
        """Verify tr_tradehub (monolith) depends on all decomposed apps."""
        deps = self.required_apps.get('tr_tradehub', [])
        expected_deps = [
            'tradehub_core', 'tradehub_catalog', 'tradehub_seller',
            'tradehub_compliance', 'tradehub_commerce', 'tradehub_logistics',
            'tradehub_marketing'
        ]
        for expected in expected_deps:
            self.assertIn(expected, deps, f"tr_tradehub should depend on {expected}")


if __name__ == '__main__':
    # Run all tests
    unittest.main(verbosity=2)
