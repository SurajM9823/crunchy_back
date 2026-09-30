import base64
from urllib.parse import urlparse, parse_qs
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from apps.customer_web.models import CustomerOrder
from apps.user_accounts.models import User
from . import test_pos
from .models import Order, PosReceipt
from .receipts import receipt_document, tracking_token


@override_settings(FRONTEND_BASE_URL='https://orders.example.test')
class ReceiptTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        test_pos.PosWorkflowTests.setUpTestData.__func__(cls)

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.manager)
        self.brand.legal_name = 'Saved Seller'
        self.brand.pan_number = '123456789'
        self.brand.save()
        self.branch.address_line = 'Saved outlet address'
        self.branch.phone_number = '9800000000'
        self.branch.save()
        result = self.client.post(f'/api/v1/orders/pos/?outlet_id={self.branch.pk}',
            {'items': [{'product_id': self.product.pk, 'quantity': 1}], 'expected_total': '200.00'},
            format='json', HTTP_IDEMPOTENCY_KEY='receipt-order')
        self.assertEqual(result.status_code, 201, result.data)
        self.order = Order.objects.get(pk=result.data['id'])
        self.receipt = PosReceipt.objects.get(order=self.order, kind='TOKEN')

    def test_receipt_keeps_saved_outlet_and_real_qr_link(self):
        self.branch.address_line = 'Changed after checkout'
        self.branch.save()
        response = self.client.get(f'/api/v1/orders/pos/receipts/{self.receipt.pk}/?outlet_id={self.branch.pk}')
        self.assertEqual(response.status_code, 200)
        doc = response.data
        self.assertEqual(doc['snapshot']['seller']['address'], 'Saved outlet address')
        self.assertEqual(doc['snapshot']['seller']['name'], 'Saved Seller')
        self.assertEqual(doc['snapshot']['seller']['phone'], '9800000000')
        self.assertEqual(doc['snapshot']['order_number'], self.order.order_number)
        self.assertEqual(urlparse(doc['tracking_url']).path, '/track')
        self.assertTrue(doc['tracking_url'].startswith('https://orders.example.test/track?'))
        svg = base64.b64decode(doc['tracking_qr'].split(',', 1)[1])
        self.assertIn(b'<svg', svg)
        self.assertIn(b'<path', svg)
        self.receipt.refresh_from_db()
        self.assertNotIn('tracking_url', self.receipt.snapshot)

    def test_scanned_receipt_tracks_only_its_order_without_customer_details(self):
        doc = receipt_document(self.receipt)
        token = parse_qs(urlparse(doc['tracking_url']).query)['token'][0]
        self.client.force_authenticate(None)
        url = '/api/v1/orders/tracking/'
        first = self.client.get(url, {'token': token})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.data['order_number'], self.order.order_number)
        for field in ['customer_phone', 'customer_name', 'delivery_address', 'payments', 'items']:
            self.assertNotIn(field, first.data)
        self.client.force_authenticate(self.manager)
        changed = self.client.post(f'/api/v1/orders/pos/{self.order.pk}/transition/?outlet_id={self.branch.pk}',
            {'version': 1, 'status': 'PREPARING'}, format='json', HTTP_IDEMPOTENCY_KEY='receipt-prepare')
        self.assertEqual(changed.status_code, 200, changed.data)
        self.client.force_authenticate(None)
        latest = self.client.get(url, {'token': token})
        self.assertEqual(latest.data['status'], 'PREPARING')
        self.assertEqual(latest.data['history'][-1]['status'], 'PREPARING')
        self.assertEqual(self.client.get(url, {'token': token+'x'}).status_code, 404)
        self.assertEqual(self.client.get(url, {'token': str(self.order.pk)}).status_code, 404)
        # Status capability cannot retrieve a receipt containing personal details.
        self.assertEqual(self.client.get('/api/v1/orders/self-service/receipt/', {'token': token}).status_code, 404)

    def test_customer_slip_is_owned_and_self_service_requires_checkout_capability(self):
        owner = User.objects.create(username='receipt-customer', role='CUSTOMER')
        other = User.objects.create(username='other-customer', role='CUSTOMER')
        CustomerOrder.objects.create(user=owner, order=self.order, request_key='owned', fingerprint='abc', receipt_image=b'x', receipt_type='image/png')
        path = f'/api/v1/orders/customer/{self.order.pk}/slip/'
        self.client.force_authenticate(other)
        self.assertEqual(self.client.get(path).status_code, 404)
        self.client.force_authenticate(owner)
        customer = self.client.get(path)
        self.assertEqual(customer.status_code, 200)
        from django.core import signing
        token = signing.dumps({'order_id': self.order.pk}, salt='self-service-order')
        self.client.force_authenticate(None)
        guest = self.client.get('/api/v1/orders/self-service/receipt/', {'token': token})
        self.assertEqual(guest.status_code, 200)
        self.assertEqual(guest.data, customer.data)

    def test_bill_and_token_share_the_tracking_destination(self):
        settled = self.client.post(f'/api/v1/orders/pos/{self.order.pk}/settle/?outlet_id={self.branch.pk}',
            {'version': 1, 'tenders': [{'method': 'CASH', 'amount': '200.00'}]},
            format='json', HTTP_IDEMPOTENCY_KEY='receipt-settlement')
        self.assertEqual(settled.status_code, 200, settled.data)
        bill = PosReceipt.objects.get(order=self.order, kind='BILL')
        self.assertEqual(receipt_document(bill)['tracking_url'], receipt_document(self.receipt)['tracking_url'])
        self.assertEqual(bill.snapshot['paid_amount'], '200.00')
        self.assertEqual(bill.snapshot['due_amount'], '0')
