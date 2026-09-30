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
