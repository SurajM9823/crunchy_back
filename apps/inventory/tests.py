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

from .models import (
    InventoryItem,
    RecipeItem,
    StockTransaction,
    StockTransactionType,
    UnitOfMeasure,
    Supplier,
    PurchaseInvoice,
    PurchaseInvoiceItem,
    StockMovementLedger,
    StockMovementType,
    DaybookAccountEntry,
    PaymentMethod,
    PaymentStatus,
)
from .services import (
    inventory_item_create,
    inventory_item_restock,
    recipe_item_create,
    deduct_inventory_for_order,
    purchase_invoice_create,
    inventory_reconcile_audit,
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
        # Moving avg: (50*25 + 20*26) / 70 = (1250 + 520) / 70 = 1770 / 70 = 25.29
        self.assertEqual(self.buns.cost_per_unit, Decimal('25.29'))

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

        # Also check StockMovementLedger
        mov = StockMovementLedger.objects.filter(branch=self.branch, reason='SALE_DEDUCTION')
        self.assertTrue(mov.exists())

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
        # Moving avg: (10*120 + 25*125) / 35 = (1200 + 3125) / 35 = 4325 / 35 = 123.57
        self.assertEqual(self.patties.cost_per_unit, Decimal('123.57'))

    def test_supplier_list_and_auto_create_api(self):
        self.client.force_authenticate(user=self.manager)

        # 1. Create a supplier
        res = self.client.post('/api/v1/inventory/suppliers/', {
            'name': 'Himalayan Dairy Suppliers',
            'phone': '9841000000',
            'pan_number': '300123456',
            'address': 'Pulchowk, Lalitpur',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data['name'], 'Himalayan Dairy Suppliers')
        sup_id = res.data['id']

        # 2. Search suppliers
        search_res = self.client.get('/api/v1/inventory/suppliers/?search=Himalayan')
        self.assertEqual(search_res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(search_res.data), 1)
        self.assertEqual(search_res.data[0]['id'], sup_id)

    def test_category_list_and_auto_create_api(self):
        self.client.force_authenticate(user=self.manager)

        # 1. Create category
        res = self.client.post('/api/v1/inventory/categories/', {
            'name': 'Bakery & Breads',
            'description': 'Buns, tortillas, and bread loaves',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data['name'], 'Bakery & Breads')

        # 2. Search categories
        search_res = self.client.get('/api/v1/inventory/categories/?search=Bakery')
        self.assertEqual(search_res.status_code, status.HTTP_200_OK)
        self.assertTrue(len(search_res.data) >= 1)

    def test_inward_purchase_invoice_and_weighted_average_cost(self):
        self.client.force_authenticate(user=self.manager)

        # Buns start at stock 50.000, cost 25.00
        # Inward purchase: 50 buns at 35.00
        # Expected new stock: 100.000
        # Expected weighted avg cost: ((50 * 25) + (50 * 35)) / 100 = (1250 + 1750) / 100 = 30.00
        payload = {
            'invoice_number': 'INV-2026-001',
            'supplier_name': 'Baker King Pvt Ltd',
            'supplier_phone': '9851122334',
            'purchase_date': '2026-09-27',
            'payment_method': 'CASH',
            'payment_status': 'PAID',
            'subtotal': '1750.00',
            'total_amount': '1750.00',
            'paid_amount': '1750.00',
            'due_amount': '0.00',
            'items': [
                {
                    'item_id': str(self.buns.id),
                    'item_name': 'Brioche Bun',
                    'quantity': '50.000',
                    'unit': 'PCS',
                    'unit_cost': '35.00',
                    'discount': '0.00',
                    'total_cost': '1750.00',
                }
            ]
        }

        res = self.client.post('/api/v1/inventory/purchases/', payload, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data['invoice_number'], 'INV-2026-001')

        self.buns.refresh_from_db()
        self.assertEqual(self.buns.current_stock, Decimal('100.000'))
        self.assertEqual(self.buns.cost_per_unit, Decimal('30.00'))
        self.assertEqual(self.buns.supplier_name, 'Baker King Pvt Ltd')

        # Verify Movement Ledger
        mov = StockMovementLedger.objects.filter(
            item=self.buns,
            reason='PURCHASE',
            reference_id='INV-2026-001',
        ).first()
        self.assertIsNotNone(mov)
        self.assertEqual(mov.quantity, Decimal('50.000'))
        self.assertEqual(mov.previous_stock, Decimal('50.000'))
        self.assertEqual(mov.new_stock, Decimal('100.000'))

    def test_inward_purchase_with_new_supplier_and_new_sku_on_the_fly(self):
        self.client.force_authenticate(user=self.manager)

        # On-the-fly registration of a completely new supplier and brand-new item
        payload = {
            'invoice_number': 'INV-FRESH-99',
            'supplier_name': 'Green Farm Fresh Veggies',
            'supplier_phone': '9801234567',
            'payment_method': 'CREDIT',
            'subtotal': '2400.00',
            'discount_amount': '0.00',
            'total_amount': '2400.00',
            'paid_amount': '0.00',
            'due_amount': '2400.00',
            'items': [
                {
                    'name': 'Fresh Iceberg Lettuce',
                    'category': 'Fresh Produce',
                    'quantity': '20.000',
                    'unit': 'KG',
                    'unit_cost': '120.00',
                    'discount': '0.00',
                    'total_cost': '2400.00',
                }
            ]
        }

        res = self.client.post('/api/v1/inventory/purchases/', payload, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

        # Verify supplier auto-created with credit balance
        sup = Supplier.objects.filter(branch=self.branch, name='Green Farm Fresh Veggies').first()
        self.assertIsNotNone(sup)
        self.assertEqual(sup.phone, '9801234567')
        self.assertEqual(sup.credit_balance, Decimal('2400.00'))

        # Verify Daybook Account Entry created for credit purchase
        dbk = DaybookAccountEntry.objects.filter(purchase__invoice_number='INV-FRESH-99').first()
        self.assertIsNotNone(dbk)
        self.assertEqual(dbk.cr_amount, Decimal('2400.00'))
        self.assertEqual(dbk.party, sup)

        # Verify new SKU auto-created
        new_item = InventoryItem.objects.filter(branch=self.branch, name='Fresh Iceberg Lettuce').first()
        self.assertIsNotNone(new_item)
        self.assertEqual(new_item.current_stock, Decimal('20.000'))
        self.assertEqual(new_item.cost_per_unit, Decimal('120.00'))
        self.assertEqual(new_item.unit, 'KG')
        self.assertEqual(new_item.category.name, 'Fresh Produce')

    def test_purchase_idempotency_key(self):
        self.client.force_authenticate(user=self.manager)

        payload = {
            'invoice_number': 'INV-IDEM-001',
            'supplier_name': 'Baker King Pvt Ltd',
            'subtotal': '500.00',
            'total_amount': '500.00',
            'paid_amount': '500.00',
            'items': [
                {
                    'item_id': str(self.buns.id),
                    'item_name': 'Brioche Bun',
                    'quantity': '10.000',
                    'unit': 'PCS',
                    'unit_cost': '50.00',
                    'total_cost': '500.00',
                }
            ]
        }

        # First request with Idempotency-Key
        res1 = self.client.post(
            '/api/v1/inventory/purchases/',
            payload,
            format='json',
            HTTP_IDEMPOTENCY_KEY='idem-key-abc-123',
        )
        self.assertEqual(res1.status_code, status.HTTP_201_CREATED)
        invoice_id = res1.data['id']

        self.buns.refresh_from_db()
        stock_after_first = self.buns.current_stock

        # Second request with SAME Idempotency-Key (simulating network retry)
        res2 = self.client.post(
            '/api/v1/inventory/purchases/',
            payload,
            format='json',
            HTTP_IDEMPOTENCY_KEY='idem-key-abc-123',
        )
        self.assertEqual(res2.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res2.data['id'], invoice_id)

        # Stock must NOT be duplicated!
        self.buns.refresh_from_db()
        self.assertEqual(self.buns.current_stock, stock_after_first)

    def test_physical_stock_audit_reconciliation(self):
        self.client.force_authenticate(user=self.manager)

        # Buns currently at 50.000. Floor audit reports actual physical count is 42.000.
        res = self.client.post('/api/v1/inventory/audits/reconcile/', {
            'items': [
                {
                    'item_id': self.buns.id,
                    'physical_stock': '42.000',
                    'note': 'Count variance on shelf 3',
                }
            ]
        }, format='json')

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['status'], 'success')

        self.buns.refresh_from_db()
        self.assertEqual(self.buns.current_stock, Decimal('42.000'))

        # Verify StockMovementLedger logged variance
        mov = StockMovementLedger.objects.filter(
            item=self.buns,
            reason='AUDIT_ADJUSTMENT',
        ).first()
        self.assertIsNotNone(mov)
        self.assertEqual(mov.type, StockMovementType.DECREASE)
        self.assertEqual(mov.quantity, Decimal('8.000'))
        self.assertEqual(mov.previous_stock, Decimal('50.000'))
        self.assertEqual(mov.new_stock, Decimal('42.000'))

    def test_stock_catalog_metrics_endpoint(self):
        self.client.force_authenticate(user=self.manager)

        # self.buns: 50 * 25.00 = 1250.00
        # self.patties: 10 * 120.00 = 1200.00
        # total_valuation = 2450.00
        res = self.client.get('/api/v1/inventory/items/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['status'], 'success')
        self.assertEqual(res.data['data']['count'], 2)
        self.assertEqual(res.data['data']['low_stock_count'], 0)
        self.assertEqual(res.data['data']['total_valuation'], 2450.00)
        self.assertEqual(len(res.data['data']['results']), 2)
