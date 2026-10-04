import io
import json
from decimal import Decimal
from PIL import Image
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from apps.orders.test_pos import PosWorkflowTests
from apps.orders.models import Order
from apps.tables.qr_security import generate_table_qr_token
from apps.user_accounts.models import User
from .models import LoyaltyCustomer, LoyaltyProgram, LoyaltyPurchase
from .services import offer, record_purchase


@override_settings(CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class LoyaltyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        PosWorkflowTests.setUpTestData.__func__(cls)
        cls.branch.enable_kiosk = True
        cls.branch.save()
        cls.brand.payment_qr = 'payment_qr/test.png'
        cls.brand.save()

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.client.force_authenticate(self.manager)
        self.counter = 0
        self.program = LoyaltyProgram.objects.create(restaurant=self.brand, enabled=True, tiers=[
            {'name': 'Silver', 'threshold': '10000.00', 'percent': '5.00'},
            {'name': 'Gold', 'threshold': '22000.00', 'percent': '10.00'},
            {'name': 'Platinum', 'threshold': '27000.00', 'percent': '15.00'}])

    def paid(self, phone='9800000000', amount='10000', source='POS', **extra):
        self.counter += 1
        values = dict(branch=self.branch, order_number=f'HISTORY-{self.counter}', is_pos_managed=True,
            customer_name='Real customer', customer_phone=phone, order_source=source, status='COMPLETED',
            payment_status='PAID', subtotal=Decimal(amount), total_payable=Decimal(amount), paid_amount=Decimal(amount))
        values.update(extra)
        return Order.objects.create(**values)

    def post(self, suffix, data, key=None):
        self.counter += 1
        return self.client.post(f'/api/v1/orders/pos/{suffix}?outlet_id={self.branch.pk}', data,
            format='json', HTTP_IDEMPOTENCY_KEY=key or f'loyalty-{self.counter}')

    def test_one_customer_across_channels_formats_and_outlets(self):
        for phone, source in [('9800000000','POS'), ('+977 980-000-0000','TABLE_QR'),
                              ('9779800000000','WEBSITE'), ('+9779800000000','KIOSK')]:
            self.paid(phone, '2500', source)
        self.assertEqual(LoyaltyCustomer.objects.count(), 1)
        self.assertEqual(offer(self.other, '9800000000', 200)['percent'], '5.00')
        self.assertIsNone(offer(self.branch, '800000000', 200))

    def test_cumulative_tiers_refunds_cancellations_and_idempotency(self):
        first = self.paid()
        record_purchase(first)
        self.assertEqual(LoyaltyPurchase.objects.count(), 1)
        self.assertEqual(offer(self.branch, first.customer_phone, 200)['amount'], '10.00')
        second = self.paid(amount='12000')
        self.assertEqual(offer(self.branch, first.customer_phone, 200)['percent'], '10.00')
        third = self.paid(amount='5000')
        self.assertEqual(offer(self.branch, first.customer_phone, 200)['percent'], '15.00')
        third.refunded_amount = Decimal('1')
        third.payment_status = 'REFUNDED'
        third.save()
        self.assertEqual(offer(self.branch, first.customer_phone, 200)['percent'], '10.00')
        second.status = 'CANCELLED'
        second.save()
        self.assertEqual(offer(self.branch, first.customer_phone, 200)['percent'], '5.00')

    def test_unpaid_partial_credit_tips_and_missing_phone_do_not_earn(self):
        self.paid(amount='10000', paid_amount=Decimal('5000'), credit_amount=Decimal('5000'))
        self.assertIsNone(offer(self.branch, '9800000000', 200))
        self.paid(phone='', amount='50000')
        self.paid(phone='invalid', amount='50000')
        self.paid(amount='10000', pricing_policy={'customer_tip': '1'})
        self.assertIsNone(offer(self.branch, '9800000000', 200))
        self.assertEqual(LoyaltyCustomer.objects.count(), 1)

    def test_cashier_gets_automatic_discount_and_retry_does_not_double_earn(self):
        self.paid()
        self.client.force_authenticate(self.cashier)
        data = {'items':[{'product_id': self.product.pk, 'quantity':1}], 'customer_phone':'+9779800000000'}
        quote = self.post('quote/', data)
        self.assertEqual(quote.status_code, 200, quote.data)
        self.assertEqual(quote.data['total_payable'], '190.00')
        data.update(expected_total='190.00', tenders=[{'method':'CASH','amount':'190.00'}])
        result = self.post('', data, 'once')
        self.assertEqual(result.status_code, 201, result.data)
        self.assertEqual(result.data['loyalty']['name'], 'Silver')
        self.assertEqual(self.post('', data, 'once').data, result.data)
        self.assertEqual(LoyaltyPurchase.objects.get(order_id=result.data['id']).amount, Decimal('190'))
        self.assertEqual(self.post('quote/', {**data, 'discount_amount':'50'}).status_code, 403)

    def test_billing_phone_automatically_reprices_and_locks_paid_bill(self):
        self.paid()
        row = self.post('', {'items':[{'product_id':self.product.pk,'quantity':1}], 'expected_total':'200'}).data
        quote = self.post(f'{row["id"]}/billing-quote/', {'version':1,'customer_phone':'9800000000','discount_amount':'0'})
        self.assertEqual(quote.data['due_amount'], '190.00')
        settled = self.post(f'{row["id"]}/settle/', {'version':1,'customer_phone':'9800000000',
            'discount_amount':'0','tenders':[{'method':'CASH','amount':'190'}]})
        self.assertEqual(settled.status_code, 200, settled.data)
        self.assertEqual(settled.data['discount_amount'], '10.00')
        frozen = self.post(f'{row["id"]}/billing-quote/', {'version':2,'discount_amount':'0'})
        self.assertEqual(frozen.data['total_payable'], '190.00')

    def test_manual_discount_does_not_stack_and_append_recalculates(self):
        self.paid()
        created = self.post('', {'items':[{'product_id':self.product.pk,'quantity':1}], 'customer_phone':'9800000000',
            'discount_amount':'30','discount_reason':'Manager offer','expected_total':'170'})
        self.assertEqual(created.status_code, 201, created.data)
        self.assertIsNone(created.data['loyalty'])
        quoted = self.post('quote/', {'order_id':created.data['id'], 'items':[{'product_id':self.product.pk,'quantity':1}]})
        self.assertEqual(quoted.data['total_payable'], '370.00')
        appended = self.post(f'{created.data["id"]}/append/', {'version':1,
            'items':[{'product_id':self.product.pk,'quantity':1}], 'expected_total':'370'})
        self.assertEqual(appended.status_code, 200, appended.data)

    def test_kiosk_and_table_qr_quotes_match_checkout(self):
        self.paid()
        self.client.force_authenticate(None)
        for source in ['KIOSK','TABLE_QR']:
            data = {'branch_id':self.branch.pk,'order_source':source,
                'fulfillment_type':'TAKEAWAY' if source=='KIOSK' else 'DINE_IN',
                'qr_token':generate_table_qr_token(self.table), 'customer_phone':'9800000000',
                'items':[{'product_id':self.product.pk,'quantity':1}]}
            quote = self.client.post('/api/v1/orders/self-service/quote/', data, format='json')
            self.assertEqual(quote.status_code, 200, quote.data)
            self.assertEqual(quote.data['total_payable'], '190.00')
            result = self.client.post('/api/v1/orders/self-service/checkout/', {**data,'expected_total':'190'},
                format='json', HTTP_IDEMPOTENCY_KEY=source)
            self.assertEqual(result.status_code, 201, result.data)
            self.assertEqual(result.data['discount_amount'], '10.00')

    def test_web_uses_authenticated_phone_and_keeps_discount_at_settlement(self):
        self.paid()
        user = User.objects.create_user(username='loyalty-web', phone_number='+9779800000000', role='CUSTOMER')
        self.client.force_authenticate(user)
        data = {'outlet_id':self.branch.pk, 'fulfillment_type':'TAKEAWAY', 'customer_name':'Web customer',
                'items':[{'product_id':self.product.pk,'quantity':1}], 'tip':'0'}
        quote = self.client.post('/api/v1/customer/checkout/quote/', data, format='json')
        self.assertEqual(quote.status_code, 200, quote.data)
        self.assertEqual(quote.data['total_payable'], '190.00')
        buffer = io.BytesIO()
        Image.new('RGB', (8,8)).save(buffer, format='PNG')
        image = SimpleUploadedFile('receipt.png', buffer.getvalue(), content_type='image/png')
        response = self.client.post('/api/v1/customer/checkout/', {'payload':json.dumps({**data,'expected_total':'190'}),
            'receipt':image}, format='multipart', HTTP_IDEMPOTENCY_KEY='web-loyalty')
        self.assertEqual(response.status_code, 201, response.data)
        self.client.force_authenticate(self.cashier)
        settled = self.post(f'{response.data["id"]}/settle/', {'version':1,'discount_amount':'0',
            'tenders':[{'method':'FONEPAY','amount':'190'}]})
        self.assertEqual(settled.status_code, 200, settled.data)
        self.assertEqual(settled.data['total_payable'], '190.00')

    def test_admin_persistence_empty_state_and_validation(self):
        path = f'/api/v1/loyalty/?outlet_id={self.branch.pk}'
        result = self.client.get(path)
        self.assertEqual(result.data['customers'], [])
        self.assertEqual(result.data['discounts'], [])
        saved = self.client.put(path, {'enabled':True,'version':1,'tiers':[
            {'name':'Real tier','threshold':'100','percent':'7'}]}, format='json')
        self.assertEqual(saved.status_code, 200, saved.data)
        self.assertEqual(self.client.get(path).data['tiers'][0]['name'], 'Real tier')
        self.assertEqual(self.client.put(path, {'enabled':True,'version':1,'tiers':[]}, format='json').status_code, 409)
        self.assertEqual(self.client.put(path, {'enabled':True,'version':2,'tiers':[
            {'name':'Invalid','threshold':'0','percent':'101'}]}, format='json').status_code, 400)
        self.client.force_authenticate(self.cashier)
        self.assertEqual(self.client.put(path, {'enabled':False,'version':2,'tiers':[]}, format='json').status_code, 403)
        self.assertEqual(self.client.get(f'/api/v1/loyalty/?outlet_id={self.other.pk}').status_code, 403)

    def test_disabled_program_and_restaurant_isolation(self):
        self.paid()
        self.program.enabled = False
        self.program.save()
        self.assertIsNone(offer(self.branch, '9800000000', 200))
        from apps.restaurants.models import Restaurant, Branch
        owner = User.objects.create(username='other-brand-owner')
        brand = Restaurant.objects.create(name='Other brand', admin=owner)
        branch = Branch.objects.create(name='Other branch', branch_code='OTHER', restaurant=brand)
        LoyaltyProgram.objects.create(restaurant=brand, enabled=True, tiers=self.program.tiers)
        self.assertIsNone(offer(branch, '9800000000', 200))


    def test_printed_bill_uses_entered_phone_before_prices_are_locked(self):
        self.paid()
        created = self.post('', {'items':[{'product_id':self.product.pk,'quantity':1}], 'expected_total':'200'}).data
        billed = self.post(f'{created["id"]}/bill/', {'version':1,'customer_phone':'9800000000','discount_amount':'0'})
        self.assertEqual(billed.status_code, 200, billed.data)
        self.assertEqual(billed.data['total_payable'], '190.00')
        self.assertEqual(billed.data['customer_phone'], '9800000000')
        self.assertIsNotNone(billed.data['billed_at'])
        self.program.enabled = False
        self.program.save()
        settled = self.post(f'{created["id"]}/settle/', {'version':2,'discount_amount':'0',
            'tenders':[{'method':'CASH','amount':'190'}]})
        self.assertEqual(settled.status_code, 200, settled.data)
        self.assertEqual(settled.data['total_payable'], '190.00')


    def test_socket_heartbeat_detects_loyalty_rule_changes(self):
        from asgiref.sync import async_to_sync
        from unittest.mock import AsyncMock
        from apps.orders.consumers import LiveDisplayConsumer
        consumer = LiveDisplayConsumer()
        consumer.outlet_id = self.branch.pk
        consumer.channel_name = 'loyalty-test'
        consumer.send = AsyncMock()
        async_to_sync(consumer.receive)(text_data='{"type":"ping"}')
        before = json.loads(consumer.send.call_args.kwargs['text_data'])['revision']
        self.program.version += 1
        self.program.save()
        async_to_sync(consumer.receive)(text_data='{"type":"ping"}')
        after = json.loads(consumer.send.call_args.kwargs['text_data'])['revision']
        self.assertNotEqual(before, after)
