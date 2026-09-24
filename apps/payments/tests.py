from decimal import Decimal
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status

from apps.user_accounts.models import UserRole
from apps.user_accounts.services import user_create
from apps.restaurants.models import OperateType
from apps.restaurants.services import restaurant_create, branch_with_admin_create
from apps.catalog.services import category_create, product_create
from apps.tables.services import table_create
from apps.orders.services import order_create_or_append_tab
from apps.orders.models import OrderStatus, PaymentStatus

from .models import PaymentTransaction, FiscalInvoice, TransactionStatus
from .services import order_settle_payment
from .receipt_generator import format_thermal_receipt


class PaymentsAndInvoicingTests(TestCase):
    def setUp(self):
        self.client = APIClient()

        # Create brand & branch with Statutory PAN
        self.owner = user_create(
            username="payment_brand_owner",
            email="owner@payment.local",
            phone_number="+9779800000051",
            password="OwnerPassword123!",
            role=UserRole.RESTAURANT_OWNER,
            is_staff=True,
        )
        self.brand = restaurant_create(
            name="Crunchy Bag Tax Corp",
            admin_user=self.owner,
            pan_number="601234567",
        )
        self.branch, self.cashier = branch_with_admin_create(
            restaurant=self.brand,
            name="Durbarmarg Branch",
            branch_code="CB-DM-01",
            operate_type=OperateType.DINE_IN,
            admin_username="cashier_binod",
            admin_phone="+9779800000052",
            admin_password="CashierPass123!",
        )

        # Dining table
        self.table = table_create(
            branch=self.branch,
            table_number="T-08",
            capacity=4,
        )

        # Products
        self.category = category_create(name="Combos", display_order=1)
        self.burger = product_create(
            category=self.category,
            name="Chicken Burger Meal",
            base_price=Decimal('500.00'),
        )

        # Place a Dine-In Order
        self.order = order_create_or_append_tab(
            branch=self.branch,
            table=self.table,
            raw_items=[{'product_id': self.burger.id, 'quantity': 2}],
            customer_name="Ramesh Thapa",
        )

    def test_order_settlement_and_fiscal_invoice_generation(self):
        self.assertEqual(self.order.status, OrderStatus.PENDING)
        self.assertEqual(self.order.payment_status, PaymentStatus.UNPAID)
        self.assertIsNotNone(self.table.active_session_id)

        # Settle order with CASH
        txn, invoice = order_settle_payment(
            order=self.order,
            payment_method="CASH",
            received_by=self.cashier,
            customer_pan="109876543",
        )

        self.assertIsNotNone(txn)
        self.assertIsNotNone(invoice)
        self.assertEqual(txn.status, TransactionStatus.SUCCESS)
        self.assertEqual(txn.amount, Decimal('1000.00'))

        # Check Order Status & Table Release
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.COMPLETED)
        self.assertEqual(self.order.payment_status, PaymentStatus.PAID)
        self.assertEqual(self.order.payment_method, "CASH")

        self.table.refresh_from_db()
        self.assertIsNone(self.table.active_session_id)

        # Check Statutory Fiscal Invoice Details
        self.assertTrue(invoice.invoice_number.startswith("INV-CB-DM-01-"))
        self.assertEqual(invoice.seller_pan, "601234567")
        self.assertEqual(invoice.customer_pan, "109876543")
        self.assertEqual(invoice.customer_name, "Ramesh Thapa")
        self.assertEqual(invoice.subtotal, Decimal('1000.00'))
        self.assertEqual(invoice.vat_amount, Decimal('115.04'))  # 1000 * 13 / 113
        self.assertEqual(invoice.taxable_amount, Decimal('884.96'))
        self.assertEqual(invoice.grand_total, Decimal('1000.00'))

    def test_idempotency_guard_prevents_duplicate_settlement(self):
        idempotency_key = "idemp_test_key_998877"

        # First settlement attempt
        txn1, inv1 = order_settle_payment(
            order=self.order,
            payment_method="CARD",
            idempotency_key=idempotency_key,
        )
        self.assertEqual(txn1.payment_method, "CARD")

        # Second settlement attempt with the SAME idempotency key
        txn2, inv2 = order_settle_payment(
            order=self.order,
            payment_method="CARD",
            idempotency_key=idempotency_key,
        )

        # Must return the SAME transaction and invoice without creating duplicates
        self.assertEqual(txn1.id, txn2.id)
        self.assertEqual(inv1.id, inv2.id)
        self.assertEqual(PaymentTransaction.objects.filter(order=self.order).count(), 1)

    def test_thermal_receipt_formatting(self):
        txn, invoice = order_settle_payment(
            order=self.order,
            payment_method="CASH",
        )

        receipt_text = format_thermal_receipt(invoice, width=42)

        # Verify key statutory components in thermal printout
        self.assertIn("CRUNCHY BAG TAX CORP", receipt_text)
        self.assertIn("Durbarmarg Branch", receipt_text)
        self.assertIn("PAN NO: 601234567", receipt_text)
        self.assertIn("TAX INVOICE", receipt_text)
        self.assertIn(invoice.invoice_number, receipt_text)
        self.assertIn("Chicken Burger Meal", receipt_text)
        self.assertIn("Included VAT (13%):", receipt_text)
        self.assertIn("GRAND TOTAL:", receipt_text)

    def test_payment_settle_api(self):
        res = self.client.post('/api/v1/payments/settle/', {
            'order_id': self.order.id,
            'payment_method': 'ESEWA',
            'gateway_ref': 'ESEWA_TXN_REF_123',
        }, format='json')

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn('invoice', res.data)
        self.assertEqual(res.data['invoice']['payment_method'], 'ESEWA')
        self.assertIn('thermal_receipt', res.data['invoice'])

    def test_digital_wallet_webhook_api(self):
        # Create fresh order
        new_order = order_create_or_append_tab(
            branch=self.branch,
            raw_items=[{'product_id': self.burger.id, 'quantity': 1}],
        )

        # Simulate eSewa webhook callback
        webhook_res = self.client.post('/api/v1/payments/webhooks/esewa/', {
            'order_number': new_order.order_number,
            'ref_id': 'ESEWA_WB_REF_777',
            'amount': '500.00',
        }, format='json')

        self.assertEqual(webhook_res.status_code, status.HTTP_200_OK)
        self.assertEqual(webhook_res.data['status'], 'success')

        new_order.refresh_from_db()
        self.assertEqual(new_order.payment_status, PaymentStatus.PAID)
        self.assertEqual(new_order.status, OrderStatus.COMPLETED)

