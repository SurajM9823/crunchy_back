from decimal import Decimal
from django.test import TestCase
from rest_framework.test import APIClient
from apps.restaurants.models import Restaurant, Branch
from apps.user_accounts.models import User
from apps.orders.models import Order, PosCreditEntry, PosReceipt, OrderOutboxEvent
from apps.payments.models import PaymentTransaction
from apps.daybook.models import DaybookEntry
from apps.daybook.serializers import today
from .models import CustomerCollection


class CustomerAccountTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='account-owner', role='RESTAURANT_OWNER')
        self.restaurant = Restaurant.objects.create(name='Customer brand', admin=self.owner)
        self.owner.restaurant = self.restaurant
        self.owner.save()
        self.branch = Branch.objects.create(restaurant=self.restaurant, name='Account outlet', branch_code='ACC1')
        other = Restaurant.objects.create(name='Other brand', admin=self.owner)
        self.other_branch = Branch.objects.create(restaurant=other, name='Other outlet', branch_code='ACC2')
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def order(self, **data):
        defaults = dict(branch=self.branch, order_number=f'ACC-{Order.objects.count()}', customer_name='Real customer',
            customer_phone='9841234567', total_payable=100, subtotal=100, is_pos_managed=True,
            order_source='POS', status='COMPLETED', credit_amount=100)
        defaults.update(data)
        return Order.objects.create(**defaults)

    def account(self, contact_id):
        result = self.client.get(f'/api/v1/customer/directory/{contact_id}/?outlet_id={self.branch.pk}')
        self.assertEqual(result.status_code, 200, result.data)
        return result.data

    def receive(self, contact_id, amount, key='receive-key', **extra):
        account = self.account(contact_id)
        return self.client.post(f'/api/v1/customer/directory/{contact_id}/receive/?outlet_id={self.branch.pk}',
            {'amount': str(amount), 'method': 'CASH', 'scope': 'CREDIT', 'date': str(today()),
             'expected_due': account['summary']['due'], 'expected_credit': account['summary']['credit'], **extra},
            format='json', HTTP_IDEMPOTENCY_KEY=key)

    def test_collection_updates_orders_credit_receipts_directory_and_daybook(self):
        first = self.order()
        second = self.order(order_source='TABLE_QR')
        response = self.receive(first.customer_contact_id, 150)
        self.assertEqual(response.status_code, 200, response.data)
        first.refresh_from_db(); second.refresh_from_db()
        self.assertEqual((first.paid_amount, first.credit_amount), (Decimal('100'), Decimal('0')))
        self.assertEqual((second.paid_amount, second.credit_amount), (Decimal('50'), Decimal('50')))
        self.assertEqual(first.payment_status, 'PAID')
        self.assertEqual(second.version, 2)
        self.assertEqual(PaymentTransaction.objects.count(), 2)
        self.assertEqual(DaybookEntry.objects.filter(direction='IN', source='SALE').count(), 2)
        self.assertEqual(PosReceipt.objects.count(), 2)
        self.assertEqual(OrderOutboxEvent.objects.filter(event_type='ORDER_SETTLE').count(), 2)
        self.assertEqual(sum(PosCreditEntry.objects.values_list('amount', flat=True)), Decimal('-150'))
        data = self.account(first.customer_contact_id)
        self.assertEqual(Decimal(data['summary']['due']), Decimal('50'))
        self.assertEqual(Decimal(data['summary']['credit']), Decimal('50'))
        self.assertEqual(Decimal(data['collections'][0]['snapshot']['remaining_due']), Decimal('50'))
        directory = self.client.get(f'/api/v1/customer/directory/?outlet_id={self.branch.pk}')
        self.assertEqual(directory.data['results'][0]['due'], Decimal('50'))
        self.assertEqual(directory.data['results'][0]['credit'], Decimal('50'))

    def test_retry_and_stale_balances_do_not_duplicate_received_money(self):
        order = self.order()
        one = self.receive(order.customer_contact_id, 25)
        two = self.receive(order.customer_contact_id, 25, expected_due='100', expected_credit='100')
        self.assertEqual(two.status_code, 200, two.data)
        self.assertEqual(one.data, two.data)
        self.assertEqual(CustomerCollection.objects.count(), 1)
        self.assertEqual(PaymentTransaction.objects.count(), 1)
        mismatch = self.receive(order.customer_contact_id, 30, expected_due='100', expected_credit='100')
        self.assertEqual(mismatch.status_code, 409)
        stale = self.receive(order.customer_contact_id, 30, key='new', expected_due='100', expected_credit='100')
        self.assertEqual(stale.status_code, 409)

    def test_legacy_paid_flags_and_successful_payments_are_not_charged_again(self):
        settled = self.order(is_pos_managed=False, credit_amount=0, payment_status='PAID')
        partial = self.order(is_pos_managed=False, credit_amount=0)
        PaymentTransaction.objects.create(branch=self.branch, order=partial, transaction_id='LEGACY-PAY', amount=40, payment_method='CASH', status='SUCCESS')
        data = self.account(settled.customer_contact_id)
        self.assertEqual(Decimal(data['summary']['due']), Decimal('60'))
        response = self.receive(partial.customer_contact_id, 20, scope='ALL')
        self.assertEqual(response.status_code, 200, response.data)
        partial.refresh_from_db()
        self.assertEqual(partial.paid_amount, Decimal('60'))
        self.assertEqual(Decimal(self.account(partial.customer_contact_id)['summary']['due']), Decimal('40'))

    def test_cancelled_orders_and_partial_refunds_do_not_distort_debt(self):
        order = self.order(total_payable=200, paid_amount=50, credit_amount=0, payment_status='REFUNDED', refunded_amount=10)
        self.order(status='CANCELLED')
        data = self.account(order.customer_contact_id)
        self.assertEqual(Decimal(data['summary']['due']), Decimal('150'))
        self.assertEqual(Decimal(data['summary']['refunded']), Decimal('10'))
        self.assertEqual(self.receive(order.customer_contact_id, 151, scope='ALL').status_code, 400)
        self.assertEqual(CustomerCollection.objects.count(), 0)

    def test_credit_only_does_not_settle_unrelated_open_orders(self):
        unpaid = self.order(credit_amount=0)
        credit = self.order(order_source='KIOSK')
        response = self.receive(credit.customer_contact_id, 75)
        self.assertEqual(response.status_code, 200, response.data)
        unpaid.refresh_from_db(); credit.refresh_from_db()
        self.assertEqual(unpaid.paid_amount, 0)
        self.assertEqual(credit.paid_amount, 75)

    def test_scope_and_receipt_access(self):
        order = self.order()
        self.assertEqual(self.client.get(f'/api/v1/customer/directory/{order.customer_contact_id}/?outlet_id={self.other_branch.pk}').status_code, 403)
        response = self.receive(order.customer_contact_id, 10)
        self.assertEqual(response.status_code, 200, response.data)
        receipt_id = response.data['snapshot']['allocations'][0]['receipt_id']
        different = self.order(customer_phone='9800000001')
        self.assertEqual(self.client.get(f'/api/v1/customer/directory/{different.customer_contact_id}/receipts/{receipt_id}/?outlet_id={self.branch.pk}').status_code, 404)
        for amount in ['0', '-1', '1.001']:
            self.assertEqual(self.receive(order.customer_contact_id, amount, key=f'bad-{amount}').status_code, 400)
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(f'/api/v1/customer/directory/{order.customer_contact_id}/?outlet_id={self.branch.pk}').status_code, [401, 403])
