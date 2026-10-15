from datetime import datetime
from zoneinfo import ZoneInfo

from django.test import TestCase
from rest_framework.test import APIClient

from apps.catalog.models import Category, Product
from apps.restaurants.models import Branch, Restaurant
from apps.tables.models import DiningTable
from apps.user_accounts.models import User
from .models import Order, OrderItem


class SalesReportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create(username='report-owner', role='RESTAURANT_OWNER')
        brand = Restaurant.objects.create(name='Reports', admin=cls.owner)
        cls.branch = Branch.objects.create(name='One', branch_code='REPORT1', restaurant=brand)
        cls.other = Branch.objects.create(name='Two', branch_code='REPORT2', restaurant=brand)
        cls.manager = User.objects.create(username='report-manager', role='BRANCH_MANAGER', branch=cls.branch)
        cls.table = DiningTable.objects.create(branch=cls.branch, table_number='A1')
        category = Category.objects.create(name='Food', restaurant=brand)
        cls.product = Product.objects.create(name='Burger', category=category, base_price=100)

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.manager)

    def order(self, day=10, hour=12, **kwargs):
        order = Order.objects.create(order_number=f'REPORT-{Order.objects.count()}',
            **{'branch': self.branch, 'table': self.table, 'status': 'COMPLETED',
               'total_payable': 200, 'paid_amount': 200, 'order_source': 'POS', **kwargs})
        Order.objects.filter(pk=order.pk).update(created_at=datetime(2026, 10, day, hour, tzinfo=ZoneInfo('Asia/Kathmandu')))
        return order

    def get_report(self, **params):
        return self.client.get('/api/v1/orders/pos/reports/sales/',
            {'outlet_id': self.branch.pk, 'start_date': '2026-10-10', 'end_date': '2026-10-11', **params})

    def test_totals_boundaries_refunds_and_no_item_fanout(self):
        order = self.order(hour=0, refunded_amount=50)
        for quantity, void in [(2, False), (3, False), (10, True)]:
            OrderItem.objects.create(order=order, product=self.product, product_name='Burger',
                quantity=quantity, unit_price=100, line_total=quantity*100, is_voided=void)
        self.order(day=11, hour=23, order_source='WEBSITE', table=None)
        self.order(day=9, hour=23)
        self.order(day=12, hour=0)
        self.order(branch=self.other)
        self.order(status='CANCELLED')
        self.order(status='PENDING')
        response = self.get_report()
        self.assertEqual(response.status_code, 200, response.data)
        report = response.data
        self.assertEqual(report['summary']['sales'], '350.00')
        self.assertEqual(report['summary']['orders'], 2)
        self.assertEqual(report['summary']['average_daily_sales'], '175.00')
        self.assertEqual(report['tables'][0]['sales'], '150.00')
        self.assertEqual(report['top_items'][0]['quantity'], 5)
        self.assertEqual(report['hourly'][0]['average_sales'], '75.00')
        self.assertEqual(len(report['channels']), 4)

    def test_empty_report_and_validation(self):
        report = self.get_report().data
        self.assertEqual(report['summary']['sales'], '0.00')
        self.assertEqual(len(report['timeline']), 2)
        self.assertEqual(len(report['hourly']), 24)
        self.assertEqual(report['top_items'], [])
        for params in [{'start_date':'invalid'}, {'start_date':'2026-10-12'},
                       {'start_date':'2020-01-01'}, {'end_date':'9999-12-31'}]:
            self.assertEqual(self.get_report(**params).status_code, 400)

    def test_authorization(self):
        self.assertEqual(self.get_report(outlet_id=self.other.pk).status_code, 403)
        self.client.force_authenticate(None)
        self.assertIn(self.get_report().status_code, (401, 403))

    def test_ranking_limits_and_stable_product_grouping(self):
        for channel in ['POS', 'WEBSITE', 'KIOSK', 'TABLE_QR']:
            order = self.order(order_source=channel)
            for n in range(8):
                product, _ = Product.objects.get_or_create(name=f'Item {n}', category=self.product.category, defaults={'base_price':100})
                OrderItem.objects.create(order=order, product=product, product_name=product.name,
                    quantity=n+1, unit_price=100, line_total=(n+1)*100)
        report = self.get_report().data
        self.assertEqual(len(report['top_items']), 7)
        self.assertEqual(report['top_items'][0]['quantity'], 32)
        for channel in report['channels']:
            self.assertEqual(len(channel['top_items']), 5)
            self.assertEqual(channel['top_items'][0]['name'], 'Item 7')
