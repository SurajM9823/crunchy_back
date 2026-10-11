from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient
from apps.user_accounts.models import User
from apps.restaurants.models import Restaurant, Branch
from apps.orders.models import Order
from apps.orders.receipts import tracking_token
from .models import CustomerOrder, OrderReview


class OrderReviewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        owner = User.objects.create_user(username='review-owner', role='RESTAURANT_OWNER')
        brand = Restaurant.objects.create(name='Review brand', admin=owner)
        branch = Branch.objects.create(name='Review outlet', restaurant=brand, branch_code='REV')
        self.order = Order.objects.create(branch=branch, order_number='W-11', status='COMPLETED', customer_phone='+9779841234567')
        CustomerOrder.objects.create(order=self.order, request_key='review-test', fingerprint='test', receipt_image=b'', receipt_type='image/png')
        self.credentials = {'order_number':'W-11', 'customer_phone':'9841234567'}

    def submit(self, **data):
        return self.client.post('/api/v1/customer/order-review/', data, format='json')

    def test_guest_review_persists_and_retries_do_not_overwrite(self):
        response = self.submit(**self.credentials, rating=4, comment='Fresh and tasty')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(OrderReview.objects.get(order=self.order).comment, 'Fresh and tasty')
        retry = self.submit(**self.credentials, rating=1, comment='Changed')
        self.assertEqual(retry.data, response.data)
        self.assertEqual(OrderReview.objects.count(), 1)
        tracked = self.client.post('/api/v1/customer/guest-orders/track/', self.credentials, format='json')
        self.assertEqual(tracked.data['review'], response.data)

    def test_receipt_token_review_and_tracking(self):
        token = tracking_token(self.order.pk)
        response = self.submit(token=token, rating=5)
        self.assertEqual(response.status_code, 200, response.data)
        tracked = self.client.get('/api/v1/orders/tracking/', {'token':token})
        self.assertEqual(tracked.data['review'], {'rating':5, 'comment':''})

    def test_unverified_and_unfinished_orders_cannot_be_reviewed(self):
        self.assertEqual(self.submit(order_number='W-11', customer_phone='9800000000', rating=5).status_code, 404)
        self.assertEqual(self.submit(token='forged', rating=5).status_code, 404)
        self.assertEqual(self.submit(order_number='W-11', rating=5).status_code, 404)
        for status in ['PENDING', 'READY', 'CANCELLED']:
            self.order.status = status
            self.order.save(update_fields=['status'])
            self.assertEqual(self.submit(**self.credentials, rating=5).status_code, 400)
        self.assertFalse(OrderReview.objects.exists())

    def test_review_input_validation(self):
        for rating in [0, 6, 'bad']:
            self.assertEqual(self.submit(**self.credentials, rating=rating).status_code, 400)
        self.assertEqual(self.submit(**self.credentials, rating=5, comment='x'*2001).status_code, 400)
        self.assertFalse(OrderReview.objects.exists())
