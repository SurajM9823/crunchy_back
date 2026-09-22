from decimal import Decimal
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status

from apps.user_accounts.models import UserRole
from apps.user_accounts.services import user_create, superuser_create
from apps.restaurants.models import OperateType
from apps.restaurants.services import restaurant_create, branch_with_admin_create

from .models import Category, Product, ProductVariant, ModifierGroup, ModifierOption, OutletProductOverride
from .services import (
    category_create,
    product_create,
    variant_create,
    modifier_group_create,
    modifier_option_create,
    outlet_toggle_product_availability,
    outlet_override_product_price,
)
from .selectors import get_outlet_menu, get_product_by_id
from .pricing_engine import (
    calculate_vat_breakdown,
    calculate_cash_round_down,
    calculate_line_item_unit_price,
    calculate_dynamic_combo_price,
)


class CatalogDomainAndPricingTests(TestCase):
    def setUp(self):
        self.client = APIClient()

        # Superadmin
        self.superuser = superuser_create(
            username="super_test",
            email="super@test.local",
            phone_number="+9779800000001",
            password="SuperPass123!",
        )

        # Brand and Outlets
        self.brand_owner = user_create(
            username="brand_test_owner",
            email="owner@test.local",
            phone_number="+9779800000002",
            password="OwnerPass123!",
            role=UserRole.RESTAURANT_OWNER,
            is_staff=True,
        )
        self.brand = restaurant_create(
            name="Crunchy Bag Burger Co",
            admin_user=self.brand_owner,
        )

        # Branch 1: Kathmandu Flagship
        self.branch1, self.manager1 = branch_with_admin_create(
            restaurant=self.brand,
            name="Kathmandu Flagship",
            branch_code="CB-KTM-01",
            operate_type=OperateType.DINE_IN,
            admin_username="ktm_admin",
            admin_phone="+9779800000011",
            admin_email="ktm@test.local",
            admin_password="AdminPass123!",
        )

        # Branch 2: Lalitpur Hub
        self.branch2, self.manager2 = branch_with_admin_create(
            restaurant=self.brand,
            name="Lalitpur Hub",
            branch_code="CB-LLP-01",
            operate_type=OperateType.EXPRESS_TAKEOUT,
            admin_username="llp_admin",
            admin_phone="+9779800000022",
            admin_email="llp@test.local",
            admin_password="AdminPass123!",
        )

        # Create Category & Product
        self.category = category_create(
            name="Artisan Burgers",
            icon_name="Utensils",
            display_order=1,
            hsn_code="996331",
        )

        self.burger = product_create(
            category=self.category,
            name="Crunchy Double Bacon",
            base_price=Decimal('450.00'),
            description="Crispy double beef patty with melted cheddar and smoked bacon.",
            prep_time_minutes=15,
            dietary_tags=["Popular", "Chef's Choice"],
        )

    def test_catalog_model_auto_ids_and_cogs(self):
        # Category ID check
        self.assertTrue(self.category.id.startswith("cat-"))

        # Product ID and COGS (~45% default)
        self.assertTrue(self.burger.id.startswith("prod-"))
        expected_cogs = (Decimal('450.00') * Decimal('0.45')).quantize(Decimal('0.01'))
        self.assertEqual(self.burger.cost_price, expected_cogs)

        # Variants
        variant = variant_create(
            product=self.burger,
            name="Single Patty",
            price=Decimal('350.00'),
            is_default=False,
        )
        self.assertTrue(variant.id.startswith("var-"))
        self.assertEqual(self.burger.variants.count(), 1)

        # Modifier Group & Options
        group = modifier_group_create(
            product=self.burger,
            name="Choose Artisan Bun",
            min_selections=1,
            max_selections=1,
            required=True,
        )
        self.assertTrue(group.id.startswith("sec-"))

        opt1 = modifier_option_create(
            group=group,
            name="Brioche Roll",
            price_delta=Decimal('0.00'),
            is_default=True,
        )
        opt2 = modifier_option_create(
            group=group,
            name="Pretzel Bun",
            price_delta=Decimal('45.00'),
            is_default=False,
        )
        self.assertTrue(opt1.id.startswith("opt-"))
        self.assertEqual(group.options.count(), 2)

    def test_statutory_vat_and_cash_round_down_engine(self):
        # 1. Tax-Inclusive Statutory 13% VAT Calculation
        subtotal = Decimal('1000.00')
        vat_data = calculate_vat_breakdown(subtotal)
        # Factor: 1000 * 13 / 113 = 115.044... -> 115.04
        self.assertEqual(vat_data['vat_amount'], Decimal('115.04'))
        self.assertEqual(vat_data['net_amount'], Decimal('884.96'))
        self.assertEqual(vat_data['gross_subtotal'], Decimal('1000.00'))

        # 2. Statutory Cash Currency Round-Down Rule (Specification 2.3)
        odd_total = Decimal('542.75')
        cash_data = calculate_cash_round_down(odd_total)
        self.assertEqual(cash_data['original_total'], Decimal('542.75'))
        self.assertEqual(cash_data['cash_round_down_savings'], Decimal('0.75'))
        self.assertEqual(cash_data['final_cash_total'], Decimal('542.00'))

    def test_dynamic_combo_pricing_engine(self):
        # Dynamic Combo Rules (Specification 4.2):
        # Base combo = NPR 400
        # Removing default fries (base price NPR 100) -> 75% credit = NPR 75 deduction
        # Adding extra sauce (price NPR 30) -> 10% combo discount = 30 * 0.90 = NPR 27 addition
        combo_price = calculate_dynamic_combo_price(
            base_combo_price=Decimal('400.00'),
            removed_items_base_prices=[Decimal('100.00')],
            extra_items_prices=[Decimal('30.00')],
            variant_upgrades_deltas=[Decimal('20.00')],
        )
        # 400 - 75 + 27 + 20 = 372
        self.assertEqual(combo_price, Decimal('372.00'))

        # Minimum Price Floor Clamp to NPR 150
        floor_combo = calculate_dynamic_combo_price(
            base_combo_price=Decimal('180.00'),
            removed_items_base_prices=[Decimal('100.00')],
        )
        # 180 - 75 = 105 -> clamped to 150
        self.assertEqual(floor_combo, Decimal('150.00'))

    def test_outlet_stock_toggle_and_menu_isolation(self):
        # Kathmandu Outlet Admin marks Crunchy Double Bacon OUT OF STOCK
        outlet_toggle_product_availability(
            branch=self.branch1,
            product=self.burger,
            is_available=False,
        )

        # 1. Check Kathmandu Flagship Menu: Item is unavailable
        ktm_menu = get_outlet_menu(branch_id=self.branch1.id, force_refresh=True)
        ktm_category = ktm_menu['categories'][0]
        ktm_item = ktm_category['products'][0]
        self.assertFalse(ktm_item['is_available'])

        # 2. Check Lalitpur Hub Menu: Item is STILL AVAILABLE! (Tenant Isolation)
        llp_menu = get_outlet_menu(branch_id=self.branch2.id, force_refresh=True)
        llp_category = llp_menu['categories'][0]
        llp_item = llp_category['products'][0]
        self.assertTrue(llp_item['is_available'])

    def test_outlet_admin_toggle_api(self):
        # Login as Kathmandu Outlet Admin
        self.client.force_authenticate(user=self.manager1)

        # Kathmandu Admin toggles burger to sold out
        res = self.client.post(f'/api/v1/catalog/outlets/me/products/{self.burger.id}/toggle-stock/', {
            'is_available': False,
            'price_override': '490.00',
        }, format='json')

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn("OUT OF STOCK", res.data['message'])
        self.assertFalse(res.data['override']['is_available'])
        self.assertEqual(res.data['override']['price_override'], '490.00')

        # Public cached menu API verification
        menu_res = self.client.get(f'/api/v1/catalog/menu/?outlet_id={self.branch1.id}')
        self.assertEqual(menu_res.status_code, status.HTTP_200_OK)
        burger_payload = menu_res.data['categories'][0]['products'][0]
        self.assertFalse(burger_payload['is_available'])
        self.assertEqual(burger_payload['base_price'], '490.00')

    def test_pricing_calculation_api(self):
        res = self.client.post('/api/v1/catalog/calculate-pricing/', {
            'subtotal': '750.50',
            'payment_method': 'CASH',
        }, format='json')

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['vat']['vat_rate_percent'], Decimal('13.00'))
        self.assertEqual(res.data['cash_rounding']['cash_round_down_savings'], Decimal('0.50'))
        self.assertEqual(res.data['final_payable_amount'], Decimal('750.00'))

