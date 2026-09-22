from decimal import Decimal
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status

from apps.user_accounts.models import UserRole
from apps.user_accounts.services import user_create, superuser_create
from apps.restaurants.models import OperateType
from apps.restaurants.services import restaurant_create, branch_with_admin_create
from apps.catalog.services import (
    category_create,
    product_create,
    variant_create,
    modifier_group_create,
    modifier_option_create,
    outlet_toggle_product_availability,
)
from apps.tables.services import table_create
from apps.tables.qr_security import generate_table_qr_token

from .models import Order, OrderItem, OrderStatus, FulfillmentType, PaymentMethod, PaymentStatus
from .services import order_create_or_append_tab, order_transition_status
from .selectors import get_kitchen_active_tickets, get_live_tv_pickup_tickets


class CentralizedOrderEngineTests(TestCase):
    def setUp(self):
        self.client = APIClient()

        # Admin & Branch
        self.owner = user_create(
            username="order_brand_owner",
            email="owner@orders.local",
            phone_number="+9779800000041",
            password="OwnerPassword123!",
            role=UserRole.RESTAURANT_OWNER,
            is_staff=True,
        )
        self.brand = restaurant_create(
            name="Crunchy Bag Order Brand",
            admin_user=self.owner,
        )
        self.branch, self.manager = branch_with_admin_create(
            restaurant=self.brand,
            name="Main Outlet",
            branch_code="CB-MAIN-01",
            operate_type=OperateType.DINE_IN,
            admin_username="main_mgr",
            admin_phone="+9779800000042",
            admin_password="ManagerPass123!",
        )

        # Dining Table
        self.table = table_create(
            branch=self.branch,
            table_number="T-05",
            capacity=4,
        )

        # Catalog setup
        self.category = category_create(name="Hot Meals", display_order=1)

        # Kitchen Item (Requires kitchen prep ticket)
        self.burger = product_create(
            category=self.category,
            name="Deluxe Smash Burger",
            base_price=Decimal('500.00'),
            requires_kitchen=True,
        )
        self.variant_double = variant_create(
            product=self.burger,
            name="Double Smash",
            price=Decimal('650.00'),
        )
        self.mod_group = modifier_group_create(
            product=self.burger,
            name="Add-ons",
        )
        self.mod_cheese = modifier_option_create(
            group=self.mod_group,
            name="Extra Cheddar",
            price_delta=Decimal('50.00'),
        )

        # Counter Direct Item (Non-kitchen item: Canned Soda)
        self.soda = product_create(
            category=self.category,
            name="Canned Coca Cola 330ml",
            base_price=Decimal('100.00'),
            requires_kitchen=False,  # Bypass KDS!
            is_counter_direct=True,
        )

    def test_single_source_of_truth_order_creation_and_taxes(self):
        # 1 item: Deluxe Smash Burger with Double variant (650) + Extra Cheddar (50) = 700 * 2 = 1400
        # 1 item: Canned Coca Cola = 100 * 1 = 100
        # Subtotal = 1500
        # Cash payment: subtotal is whole integer, cash savings = 0.00
        # 13% tax-inclusive VAT: 1500 * (13 / 113) = 172.57
        order = order_create_or_append_tab(
            branch=self.branch,
            table=self.table,
            raw_items=[
                {
                    'product_id': self.burger.id,
                    'variant_id': self.variant_double.id,
                    'modifier_option_ids': [self.mod_cheese.id],
                    'quantity': 2,
                    'item_notes': 'Well done',
                },
                {
                    'product_id': self.soda.id,
                    'quantity': 1,
                }
            ],
            payment_method=PaymentMethod.CASH,
            customer_name="Sita Sharma",
        )

        self.assertEqual(order.subtotal, Decimal('1500.00'))
        self.assertEqual(order.total_payable, Decimal('1500.00'))
        self.assertEqual(order.vat_included_amount, Decimal('172.57'))
        self.assertEqual(order.items.count(), 2)

        # Verify Order Number Format: CB-MAIN-01-YYMMDD-0001
        self.assertTrue(order.order_number.startswith("CB-MAIN-01-"))

    def test_kds_kitchen_ticket_separation_rule_2(self):
        # Place order with 1 kitchen item and 1 non-kitchen item
        order = order_create_or_append_tab(
            branch=self.branch,
            table=self.table,
            raw_items=[
                {'product_id': self.burger.id, 'quantity': 1},
                {'product_id': self.soda.id, 'quantity': 2},
            ]
        )

        # Query KDS active preparation tickets
        kitchen_tickets = list(get_kitchen_active_tickets(self.branch.id))
        self.assertEqual(len(kitchen_tickets), 1)

        kds_order = kitchen_tickets[0]
        kds_items = list(kds_order.items.all())

        # KDS items MUST contain ONLY the burger, NOT the soda!
        self.assertEqual(len(kds_items), 1)
        self.assertEqual(kds_items[0].product_id, self.burger.id)
        self.assertTrue(kds_items[0].requires_kitchen)

    def test_running_table_tab_appends_rounds(self):
        # Round 1: Customer orders 1 burger
        order_round1 = order_create_or_append_tab(
            branch=self.branch,
            table=self.table,
            fulfillment_type=FulfillmentType.DINE_IN,
            raw_items=[
                {'product_id': self.burger.id, 'quantity': 1},
            ]
        )
        self.assertEqual(order_round1.subtotal, Decimal('500.00'))
        self.assertEqual(order_round1.items.first().round_number, 1)

        # Round 2: 15 minutes later, customer orders 2 sodas from the same table
        order_round2 = order_create_or_append_tab(
            branch=self.branch,
            table=self.table,
            fulfillment_type=FulfillmentType.DINE_IN,
            raw_items=[
                {'product_id': self.soda.id, 'quantity': 2},
            ]
        )

        # Must be the SAME unified order object with updated subtotal
        self.assertEqual(order_round1.id, order_round2.id)
        self.assertEqual(order_round2.subtotal, Decimal('700.00'))
        self.assertEqual(order_round2.items.count(), 2)

        soda_item = order_round2.items.filter(product_id=self.soda.id).first()
        self.assertEqual(soda_item.round_number, 2)

    def test_out_of_stock_item_rejected_at_checkout(self):
        # Outlet Admin marks burger out of stock
        outlet_toggle_product_availability(
            branch=self.branch,
            product=self.burger,
            is_available=False,
        )

        # Checkout request MUST be rejected
        with self.assertRaises(Exception) as ctx:
            order_create_or_append_tab(
                branch=self.branch,
                raw_items=[
                    {'product_id': self.burger.id, 'quantity': 1},
                ]
            )
        self.assertIn("OUT OF STOCK", str(ctx.exception))

    def test_order_status_transitions_and_history(self):
        order = order_create_or_append_tab(
            branch=self.branch,
            table=self.table,
            raw_items=[{'product_id': self.burger.id, 'quantity': 1}],
        )
        self.assertEqual(order.status, OrderStatus.PENDING)

        # Transition: PENDING -> ACCEPTED
        order_transition_status(order, OrderStatus.ACCEPTED, user=self.manager)
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.ACCEPTED)

        # Transition: ACCEPTED -> PREPARING
        order_transition_status(order, OrderStatus.PREPARING, user=self.manager)
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.PREPARING)

        # Check TV Display Query contains this order under 'preparing'
        tv_tickets = get_live_tv_pickup_tickets(self.branch.id)
        self.assertIn(order.order_number, tv_tickets['preparing'])

        # Transition: PREPARING -> READY
        order_transition_status(order, OrderStatus.READY, user=self.manager)
        tv_tickets_ready = get_live_tv_pickup_tickets(self.branch.id)
        self.assertIn(order.order_number, tv_tickets_ready['ready'])

        # Transition: READY -> COMPLETED
        order_transition_status(order, OrderStatus.COMPLETED, user=self.manager)
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.COMPLETED)
        self.assertEqual(order.payment_status, PaymentStatus.PAID)

        # Verify Table Session closed
        self.table.refresh_from_db()
        self.assertIsNone(self.table.active_session_id)

        # Verify Immutable Audit History
        self.assertEqual(order.status_history.count(), 5)

    def test_checkout_api_via_table_qr_token(self):
        qr_token = generate_table_qr_token(self.table)

        # Mobile customer posts checkout with table QR token
        res = self.client.post('/api/v1/orders/checkout/', {
            'qr_token': qr_token,
            'fulfillment_type': 'DINE_IN',
            'customer_name': 'Mobile Customer',
            'payment_method': 'CASH',
            'items': [
                {'product_id': self.burger.id, 'quantity': 1}
            ]
        }, format='json')

        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data['table_number'], "T-05")
        self.assertEqual(res.data['total_payable'], "500.00")
        self.assertEqual(res.data['status'], OrderStatus.PENDING)

