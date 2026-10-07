import uuid
from datetime import timedelta
from django.test import TestCase
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APIClient
from apps.restaurants.models import Restaurant, Branch
from apps.user_accounts.models import User
from apps.orders.models import Order
from .models import CustomerContact, WebsiteVisit
from .audience_services import record_login


class AudienceTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.owner = User.objects.create_user(username='owner', role='RESTAURANT_OWNER')
        self.org = Restaurant.objects.create(name='Brand', admin=self.owner)
        self.owner.restaurant = self.org
        self.owner.save()
        self.branch = Branch.objects.create(restaurant=self.org, name='Outlet', branch_code='AUD1')
        self.other_org = Restaurant.objects.create(name='Other brand', admin=self.owner)
        self.other_branch = Branch.objects.create(restaurant=self.other_org, name='Other outlet', branch_code='AUD2')

    def event(self, **extra):
        return {'event_id':str(uuid.uuid4()), 'visitor_id':str(uuid.uuid4()), 'session_id':str(uuid.uuid4()),
                'outlet_id':self.branch.pk, 'path':'/menu', 'channel':'WEBSITE','device':'MOBILE', **extra}

    def test_pos_and_qr_reuse_contact_and_search_is_private(self):
        for index, source in enumerate(['POS', 'TABLE_QR', 'POS']):
            Order.objects.create(branch=self.branch, order_number=f'CONTACT-{index}',
                customer_name='Returning Customer', customer_phone='9841234567' if index == 0 else '+9779841234567',
                order_source=source)
        contact = CustomerContact.objects.get(branch=self.branch)
        self.assertEqual(contact.sources, ['POS', 'TABLE_QR'])
        url = f'/api/v1/orders/pos/customers/?outlet_id={self.branch.pk}'
        self.assertIn(self.client.get(url).status_code, [401, 403])
        self.client.force_authenticate(self.owner)
        for search in ['Returning', '984 123 4567']:
            response = self.client.get(url, {'outlet_id': self.branch.pk, 'search': search})
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['results'][0]['phone'], '+9779841234567')
        self.assertEqual(self.client.get(url, {'outlet_id': self.branch.pk, 'search': 'Unknown'}).data['results'], [])
        self.assertEqual(self.client.get(f'/api/v1/orders/pos/customers/?outlet_id={self.other_branch.pk}').status_code, 403)

    def test_traffic_is_deduplicated_hashed_and_aggregated(self):
        payload = self.event()
        for unused in range(2):
            self.assertEqual(self.client.post('/api/v1/customer/traffic/',payload,format='json').status_code,204)
        visit = WebsiteVisit.objects.get()
        self.assertNotEqual(visit.visitor_hash, payload['visitor_id'])
        payload['event_id'] = str(uuid.uuid4())
        payload['path'] = '/orders'
        self.client.post('/api/v1/customer/traffic/',payload,format='json')
        self.client.force_authenticate(self.owner)
        response = self.client.get(f'/api/v1/customer/analytics/?outlet_id={self.branch.pk}&days=7')
        self.assertEqual(response.status_code,200,response.data)
        self.assertEqual(response.data['page_views'],2)
        self.assertEqual(response.data['visitors'],1)
        self.assertEqual(response.data['sessions'],1)
        self.assertEqual(len(response.data['daily']),7)
        self.assertEqual(response['Cache-Control'],'private, no-store')

    def test_directory_includes_all_channels_and_phone_formats(self):
        phones = ['9841234567', '+1 (415) 555-0123', '01-5551234', '88888']
        for index, source in enumerate(['POS', 'TABLE_QR', 'KIOSK', 'WEBSITE']):
            Order.objects.create(branch=self.branch, order_number=f'ALL-{index}',
                customer_phone=phones[index], customer_name=f'Customer {index}',
                order_source=source, total_payable=100)
        self.client.force_authenticate(self.owner)
        response = self.client.get('/api/v1/customer/directory/', {'outlet_id': self.branch.pk})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['count'], 4)
        self.assertTrue(all(row['orders'] == 1 and row['order_total'] == 100 for row in response.data['results']))
        for phone in phones:
            result = self.client.get('/api/v1/customer/directory/', {'outlet_id': self.branch.pk, 'search': phone})
            self.assertEqual(result.data['count'], 1, result.data)

    def test_name_only_contacts_are_retained_without_merging_into_phone_contacts(self):
        for index, (name, phone) in enumerate([
            ('  Alex   Smith ', ''), ('alex smith', ''), ('Alex Smith', '9841234567'),
            ('Guest', ''), ('Walk-in Guest', ''), ('', ''),
        ]):
            Order.objects.create(branch=self.branch, order_number=f'NAME-{index}',
                customer_phone=phone, customer_name=name, order_source='POS')
        self.assertEqual(CustomerContact.objects.filter(branch=self.branch).count(), 2)
        name_only = CustomerContact.objects.get(branch=self.branch, phone='')
        self.assertEqual(name_only.name_key, 'alex smith')
        self.assertEqual(name_only.orders.count(), 2)
        self.assertEqual(CustomerContact.objects.get(phone='+9779841234567').orders.count(), 1)

    def test_billing_contact_change_moves_order_totals(self):
        order = Order.objects.create(branch=self.branch, order_number='BILL-CONTACT',
            customer_name='Alex', customer_phone='9841234567', order_source='POS', total_payable=50)
        previous = order.customer_contact_id
        order.customer_phone = '9801234567'
        order.save(update_fields=['customer_phone'])
        self.assertNotEqual(order.customer_contact_id, previous)
        self.assertEqual(CustomerContact.objects.get(pk=previous).orders.count(), 0)
        self.assertEqual(order.customer_contact.orders.count(), 1)

    def test_backfill_restores_missing_orders_and_is_idempotent(self):
        from importlib import import_module
        from django.apps import apps
        from django.db import connection
        from types import SimpleNamespace
        # bulk_create represents historic records saved before contact capture existed.
        Order.objects.bulk_create([
            Order(branch=self.branch, order_number=f'OLD-{index}', customer_name=name,
                  customer_phone=phone, order_source=source)
            for index, (source, phone, name) in enumerate([
                ('POS', '9841234567', 'Local'), ('TABLE_QR', '+9779841234567', 'Local'),
                ('KIOSK', '+44 20 7946 0958', 'Visitor'), ('WEBSITE', '', 'Name only'),
                ('POS', '01-5551234', 'Landline'), ('POS', '', 'Guest'),
            ])
        ])
        backfill = import_module('apps.customer_web.migrations.0010_backfill_all_order_contacts').backfill
        for _ in range(2):
            backfill(apps, SimpleNamespace(connection=connection))
            self.assertEqual(CustomerContact.objects.count(), 4)
            self.assertEqual(Order.objects.filter(customer_contact__isnull=False).count(), 5)
        self.assertEqual(CustomerContact.objects.get(phone='+9779841234567').orders.count(), 2)

    def test_private_routes_and_scope_are_enforced(self):
        for path in ['analytics','directory']:
            url = f'/api/v1/customer/{path}/?outlet_id={self.branch.pk}'
            self.assertIn(self.client.get(url).status_code,[401,403])
            self.client.force_authenticate(self.owner)
            self.assertEqual(self.client.get(f'/api/v1/customer/{path}/?outlet_id={self.other_branch.pk}').status_code,403)
            self.client.force_authenticate(None)
        result = self.client.post('/api/v1/customer/traffic/', self.event(path='/track?token=secret'), format='json')
        self.assertEqual(result.status_code,400)
        self.assertFalse(WebsiteVisit.objects.exists())

    def test_web_login_and_guest_orders_merge_by_outlet_phone(self):
        user = User.objects.create_user(username='Suraj', role='CUSTOMER', phone_number='+9779841234567')
        record_login(user,self.branch.pk)
        for i, source in enumerate(['TABLE_QR','KIOSK','WEBSITE']):
            Order.objects.create(branch=self.branch, order_number=f'AUD-{i}', customer_phone='9841234567' if i<2 else '+9779841234567',
                customer_name='Suraj',order_source=source,status='CANCELLED' if i==2 else 'ACCEPTED',total_payable=100)
        Order.objects.create(branch=self.other_branch, order_number='OTHER-1',customer_phone='9841234567',customer_name='Other',order_source='KIOSK',total_payable=999)
        self.assertEqual(CustomerContact.objects.filter(branch=self.branch).count(),1)
        self.client.force_authenticate(self.owner)
        result = self.client.get(f'/api/v1/customer/directory/?outlet_id={self.branch.pk}&search=9841234567')
        self.assertEqual(result.status_code,200,result.data)
        row = result.data['results'][0]
        self.assertEqual(row['orders'],3)
        self.assertEqual(row['order_total'],200)
        self.assertEqual(set(row['sources']),{'WEBSITE','TABLE_QR','KIOSK'})
        self.assertTrue(row['registered'])
        self.assertIsNotNone(row['last_login'])
        self.assertNotIn('password',str(result.data))

    def test_date_window_zero_fill_and_pagination(self):
        for i in range(28):
            CustomerContact.objects.create(branch=self.branch,phone=f'+97798000000{i:02}',name=f'Guest {i}')
        self.client.force_authenticate(self.owner)
        result = self.client.get(f'/api/v1/customer/directory/?outlet_id={self.branch.pk}&page=2')
        self.assertEqual(result.data['count'],28)
        self.assertEqual(len(result.data['results']),3)
        result = self.client.get(f'/api/v1/customer/analytics/?outlet_id={self.branch.pk}&days=30')
        self.assertEqual(result.data['visitors'],0)
        self.assertEqual(len(result.data['daily']),30)
