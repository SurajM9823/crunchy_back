from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from apps.user_accounts.models import User
from apps.customer_web.models import CustomerOrder
from apps.daybook.models import DaybookEntry
from apps.payments.models import FiscalInvoice, PaymentTransaction
from apps.inventory.models import StockTransaction, StockMovementLedger
from apps.loyalty.models import LoyaltyPurchase
from . import test_pos
from .models import Order, PosReceipt, PosCreditEntry, PosMutation, OrderOutboxEvent
from .tasks import publish_pos_events


@override_settings(CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class OrderDeletionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        test_pos.PosWorkflowTests.setUpTestData.__func__(cls)

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.payload = {'items': [{'product_id': self.product.pk, 'quantity': 1}],
                        'expected_total': '200.00', 'customer_phone': '9800000011',
                        'tenders': [{'method': 'CASH', 'amount': '200'}]}
        self.root = f'/api/v1/orders/pos/?outlet_id={self.branch.pk}'
        response = self.client.post(self.root, self.payload, format='json', HTTP_IDEMPOTENCY_KEY='create-delete-test')
        self.assertEqual(response.status_code, 201, response.data)
        self.order = Order.objects.get(pk=response.data['id'])
        self.url = f'/api/v1/orders/pos/{self.order.pk}/delete/?outlet_id={self.branch.pk}'
        self.data = {'version': self.order.version, 'confirmation': self.order.order_number, 'restore_stock': False}

    def delete(self, **changes):
        return self.client.post(self.url, {**self.data, **changes}, format='json', HTTP_IDEMPOTENCY_KEY='delete-test')

    def test_deletes_complete_bill_dependents_and_retries_without_recreation(self):
        order_id = self.order.pk
        payment = self.order.payments.get()
        DaybookEntry.objects.create(branch=self.branch, date='2026-10-06', direction='IN', amount=200,
            payment_method='CASH', category='Sale', description='Sale', source='SALE', payment=payment, recorded_by=self.owner)
        FiscalInvoice.objects.create(order=self.order, branch=self.branch, restaurant=self.brand,
            invoice_number='DELETE-INVOICE', seller_pan='123', subtotal=200, taxable_amount=200,
            vat_amount=0, grand_total=200, payment_method='CASH')
        PosCreditEntry.objects.create(order=self.order, amount=10, customer_phone='9800000011', reason='test', actor=self.owner)
        customer = User.objects.create(username='delete-customer', role='CUSTOMER')
        CustomerOrder.objects.create(user=customer, order=self.order, request_key='web-request', fingerprint='hash',
                                     receipt_image=b'private receipt', receipt_type='image/png')
        Order.objects.filter(pk=order_id).update(status='COMPLETED')
        self.assertTrue(LoyaltyPurchase.objects.filter(order_id=order_id).exists())
        result = self.delete()
        self.assertEqual(result.status_code, 200, result.data)
        self.assertFalse(Order.objects.filter(pk=order_id).exists())
        for model in (PaymentTransaction, FiscalInvoice, PosReceipt, PosCreditEntry, CustomerOrder, LoyaltyPurchase):
            self.assertFalse(model.objects.filter(order_id=order_id).exists(), model.__name__)
        self.assertFalse(DaybookEntry.objects.filter(payment_id=payment.pk).exists())
        self.assertFalse(StockTransaction.objects.filter(reference_order_id=order_id).exists())
        self.assertFalse(StockMovementLedger.objects.filter(reference_id=self.order.order_number).exists())
        self.assertEqual(self.delete().data, result.data)
        self.assertEqual(self.delete(restore_stock=True).status_code, 409)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.current_stock, 19)  # Used food does not return automatically.
        retry = self.client.post(self.root, self.payload, format='json', HTTP_IDEMPOTENCY_KEY='create-delete-test')
        self.assertEqual(retry.status_code, 409)
        self.assertFalse(Order.objects.filter(pk=order_id).exists())
        self.assertEqual(PosMutation.objects.get(key='create-delete-test').response, {'deleted': True})

    def test_restore_stock_and_durable_deletion_event(self):
        result = self.delete(restore_stock=True)
        self.assertEqual(result.status_code, 200, result.data)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.current_stock, 20)
        self.delete(restore_stock=True)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.current_stock, 20)
        event = OrderOutboxEvent.objects.get(event_type='ORDER_DELETED')
        self.assertIsNone(event.order_id)
        with patch('apps.orders.tasks.async_to_sync') as send:
            publish_pos_events()
        event.refresh_from_db()
        self.assertIsNotNone(event.published_at)
        groups = [call.args[0] for call in send.return_value.call_args_list]
        self.assertIn(f'pos_{self.branch.pk}', groups)
        self.assertIn(f'order_{self.order.pk}', groups)

    def test_permissions_and_outlet_scope(self):
        for user in [self.manager, self.cashier, self.chef]:
            self.client.force_authenticate(user)
            self.assertEqual(self.delete().status_code, 403)
        self.client.force_authenticate(self.owner)
        original = self.url
        self.url = original.replace(f'outlet_id={self.branch.pk}', f'outlet_id={self.other.pk}')
        self.assertEqual(self.delete().status_code, 404)
        self.assertTrue(Order.objects.filter(pk=self.order.pk).exists())
        outsider = User.objects.create(username='other-owner', role='RESTAURANT_OWNER')
        self.client.force_authenticate(outsider)
        self.url = original
        self.assertEqual(self.delete().status_code, 403)
        self.client.force_authenticate(None)
        self.assertIn(self.delete().status_code, [401, 403])

    def test_superadmin_can_delete(self):
        admin = User.objects.create(username='delete-superadmin', role='SUPERADMIN')
        self.client.force_authenticate(admin)
        self.assertEqual(self.delete().status_code, 200)

    def test_stale_or_unconfirmed_deletion_changes_nothing(self):
        self.assertEqual(self.delete(version=999).status_code, 409)
        self.assertEqual(self.delete(confirmation='wrong').status_code, 400)
        self.assertTrue(self.order.payments.exists())
        self.assertTrue(self.order.pos_receipts.exists())
        self.assertTrue(Order.objects.filter(pk=self.order.pk).exists())

    def test_failure_rolls_back_all_related_deletions(self):
        with patch.object(Order, 'delete', side_effect=RuntimeError('simulated failure')):
            with self.assertRaises(RuntimeError):
                self.delete(restore_stock=True)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.current_stock, 19)
        self.assertTrue(self.order.payments.exists())
        self.assertTrue(self.order.pos_receipts.exists())
        self.assertTrue(StockTransaction.objects.filter(reference_order=self.order).exists())
        self.assertEqual(PosMutation.objects.get(key='create-delete-test').response['id'], self.order.pk)

    def test_other_orders_and_their_payments_are_untouched(self):
        sibling = self.client.post(self.root, self.payload, format='json', HTTP_IDEMPOTENCY_KEY='sibling')
        self.assertEqual(sibling.status_code, 201)
        sibling_id = sibling.data['id']
        self.assertEqual(self.delete(restore_stock=True).status_code, 200)
        self.assertTrue(Order.objects.filter(pk=sibling_id).exists())
        self.assertTrue(PaymentTransaction.objects.filter(order_id=sibling_id).exists())
        self.assertTrue(PosReceipt.objects.filter(order_id=sibling_id).exists())
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.current_stock, 19)

    def test_employee_superadmin_is_scoped_and_must_be_active(self):
        from apps.user_accounts.models import Employee
        employee = Employee.objects.create(id='scoped-admin', user=self.cashier, name='Scoped admin',
            email='scoped@example.test', phone='9800000012', role='SUPER_ADMIN',
            assigned_outlet=self.other, assigned_pages=['pos'])
        self.client.force_authenticate(User.objects.get(pk=self.cashier.pk))
        self.assertEqual(self.delete().status_code, 403)
        employee.assigned_outlet = self.branch
        employee.is_active = False
        employee.save()
        self.client.force_authenticate(User.objects.get(pk=self.cashier.pk))
        self.assertEqual(self.delete().status_code, 403)
        employee.is_active = True
        employee.save()
        self.client.force_authenticate(User.objects.get(pk=self.cashier.pk))
        self.assertEqual(self.delete().status_code, 200)

    def test_deleted_table_session_is_released(self):
        import uuid
        session = uuid.uuid4()
        self.table.active_session_id = session
        self.table.save()
        Order.objects.filter(pk=self.order.pk).update(table=self.table, table_session_id=session)
        self.assertEqual(self.delete().status_code, 200)
        self.table.refresh_from_db()
        self.assertIsNone(self.table.active_session_id)
