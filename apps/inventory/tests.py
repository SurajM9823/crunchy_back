from decimal import Decimal
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status

from apps.user_accounts.models import UserRole
from apps.user_accounts.services import user_create
from apps.restaurants.models import OperateType
from apps.restaurants.services import restaurant_create, branch_with_admin_create
from apps.catalog.models import OutletProductOverride
from apps.catalog.services import category_create, product_create, variant_create
from apps.orders.services import order_create_or_append_tab

from .models import InventoryItem, RecipeItem, StockTransaction, StockTransactionType, UnitOfMeasure
from .services import (
    inventory_item_create,
    inventory_item_restock,
    recipe_item_create,
    deduct_inventory_for_order,
)
from .selectors import list_low_stock_items, get_inventory_item_by_sku


class AtomicInventoryAndRecipeTests(TestCase):
    def setUp(self):
        self.client = APIClient()

        # Brand and Outlets
        self.owner = user_create(
            username="inv_brand_owner",
            email="owner@inv.local",
            phone_number="+9779800000061",
            password="OwnerPassword123!",
            role=UserRole.RESTAURANT_OWNER,
            is_staff=True,
        )
        self.brand = restaurant_create(
            name="Crunchy Bag Inventory Brand",
            admin_user=self.owner,
        )
        self.branch, self.manager = branch_with_admin_create(
            restaurant=self.brand,
            name="Jawalakhel Branch",
            branch_code="CB-JW-01",
            operate_type=OperateType.DINE_IN,
            admin_username="jawalakhel_mgr",
            admin_phone="+9779800000062",
            admin_password="ManagerPass123!",
        )

        # Raw Stock Items
        self.buns = inventory_item_create(
            branch=self.branch,
            sku="SKU-BUN-BRIOCHE",
            name="Brioche Bun",
            current_stock=Decimal('50.000'),
            unit=UnitOfMeasure.PCS,
            min_threshold=Decimal('10.000'),
            cost_per_unit=Decimal('25.00'),
        )
        self.patties = inventory_item_create(
            branch=self.branch,
            sku="SKU-BEEF-PATTY-150G",
            name="Beef Patty 150g",
            current_stock=Decimal('10.000'),
            unit=UnitOfMeasure.PCS,
            min_threshold=Decimal('5.000'),
            cost_per_unit=Decimal('120.00'),
        )

        # Catalog Menu Items
        self.cat = category_create(name="Burgers", display_order=1)
        self.burger = product_create(
            category=self.cat,
            name="Double Smash Burger",
            base_price=Decimal('600.00'),
        )

        # Recipe Bill of Materials:
        # 1 Double Smash Burger = 1 Brioche Bun + 2 Beef Patties
        self.recipe_bun = recipe_item_create(
            product=self.burger,
            inventory_item=self.buns,
            quantity_required=Decimal('1.000'),
        )
        self.recipe_patty = recipe_item_create(
            product=self.burger,
            inventory_item=self.patties,
            quantity_required=Decimal('2.000'),
        )

    def test_stock_creation_and_restock_service(self):
        self.assertEqual(self.buns.current_stock, Decimal('50.000'))
        self.assertEqual(StockTransaction.objects.filter(inventory_item=self.buns).count(), 1)

        # Restock 20 buns
        inventory_item_restock(
            item=self.buns,
            quantity=Decimal('20.000'),
            cost_per_unit=Decimal('26.00'),
            performed_by=self.manager,
            notes="Morning bakery intake",
        )
        self.buns.refresh_from_db()
        self.assertEqual(self.buns.current_stock, Decimal('70.000'))
        self.assertEqual(self.buns.cost_per_unit, Decimal('26.00'))

        # Check StockTransaction audit log
        latest_txn = self.buns.transactions.order_by('-id').first()
        self.assertEqual(latest_txn.transaction_type, StockTransactionType.RESTOCK_PURCHASE)
        self.assertEqual(latest_txn.quantity, Decimal('20.000'))
        self.assertEqual(latest_txn.resulting_stock, Decimal('70.000'))

    def test_atomic_stock_deduction_for_order(self):
        # Order 3 Double Smash Burgers
        # Deduction expected:
        # 3 * 1 = 3 Brioche Buns (50 - 3 = 47)
        # 3 * 2 = 6 Beef Patties (10 - 6 = 4)
        order = order_create_or_append_tab(
            branch=self.branch,
            raw_items=[{'product_id': self.burger.id, 'quantity': 3}],
        )

        deductions = deduct_inventory_for_order(order)
        self.assertEqual(len(deductions), 2)

        self.buns.refresh_from_db()
        self.patties.refresh_from_db()

        self.assertEqual(self.buns.current_stock, Decimal('47.000'))
        self.assertEqual(self.patties.current_stock, Decimal('4.000'))

    def test_auto_out_of_stock_trigger_when_stock_depleted(self):
        # Current stock of patties is 10
        # Customer orders 5 Double Smash Burgers -> consumes 5 * 2 = 10 patties!
        # Resulting stock of patties reaches 0.000
        order = order_create_or_append_tab(
            branch=self.branch,
            raw_items=[{'product_id': self.burger.id, 'quantity': 5}],
        )

        deduct_inventory_for_order(order)

        self.patties.refresh_from_db()
        self.assertEqual(self.patties.current_stock, Decimal('0.000'))

        # Auto Out-Of-Stock Trigger MUST have marked the burger out of stock for this branch!
        override = OutletProductOverride.objects.filter(branch=self.branch, product=self.burger).first()
        self.assertIsNotNone(override)
        self.assertFalse(override.is_available)

    def test_low_stock_selector(self):
        # Patties start at 10 (min_threshold is 5)
        self.assertFalse(self.patties.is_low_stock)

        # Order 4 burgers -> 4 * 2 = 8 patties consumed -> 10 - 8 = 2 remaining!
        order = order_create_or_append_tab(
            branch=self.branch,
            raw_items=[{'product_id': self.burger.id, 'quantity': 4}],
        )
        deduct_inventory_for_order(order)

        self.patties.refresh_from_db()
        self.assertEqual(self.patties.current_stock, Decimal('2.000'))
        self.assertTrue(self.patties.is_low_stock)

        low_stock_list = list(list_low_stock_items(self.branch.id))
        self.assertEqual(len(low_stock_list), 1)
        self.assertEqual(low_stock_list[0].id, self.patties.id)

    def test_inventory_restock_api(self):
        self.client.force_authenticate(user=self.manager)

        res = self.client.post(f'/api/v1/inventory/items/{self.patties.id}/restock/', {
            'quantity': '25.000',
            'cost_per_unit': '125.00',
            'notes': 'Supplier delivery invoice #9901',
        }, format='json')

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.patties.refresh_from_db()
        # 10 + 25 = 35
        self.assertEqual(self.patties.current_stock, Decimal('35.000'))
        self.assertEqual(self.patties.cost_per_unit, Decimal('125.00'))

