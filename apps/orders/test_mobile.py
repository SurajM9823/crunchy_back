from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase, override_settings

from rest_framework.test import APIClient

from . import test_pos
from .models import Order
from apps.user_accounts.models import Employee, User


@override_settings(CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class MobilePortalTests(TestCase):
    setUpTestData = classmethod(test_pos.PosWorkflowTests.setUpTestData.__func__)
    setUp = test_pos.PosWorkflowTests.setUp
    path = test_pos.PosWorkflowTests.path
    post = test_pos.PosWorkflowTests.post
    create = test_pos.PosWorkflowTests.create

    def test_dashboard_defaults_to_nepal_today_and_supports_history(self):
        current = self.create()
        previous = self.create()
        Order.objects.filter(pk=current['id']).update(created_at=datetime(2026, 10, 7, 18, 15, tzinfo=timezone.utc))
        Order.objects.filter(pk=previous['id']).update(created_at=datetime(2026, 10, 7, 18, 14, tzinfo=timezone.utc))
        with patch('django.utils.timezone.now', return_value=datetime(2026, 10, 7, 20, tzinfo=timezone.utc)):
            response = self.client.get(self.path('dashboard/'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual([r['id'] for r in response.data['results']], [current['id']])
        self.assertEqual(Decimal(response.data['summary']['final']), Decimal('200.00'))
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        response = self.client.get(self.path('dashboard/') + '&all_dates=true')
        self.assertEqual(response.data['count'], 2)
        self.assertEqual(self.client.get(self.path('dashboard/') + '&all_dates=true&start_date=2026-10-08').status_code, 400)
        self.assertEqual(self.client.get(self.path('dashboard/') + '&start_date=invalid').status_code, 400)
        self.assertEqual(self.client.get(f'/api/v1/orders/pos/dashboard/?outlet_id={self.other.pk}').status_code, 403)

    def test_waiter_password_login_and_jwt_order_access(self):
        self.create()
        User.objects.create_user(username='mobile-waiter', password='Mobile-test-123!', role='WAITER', branch=self.branch, restaurant=self.brand)
        client = APIClient()
        self.assertEqual(client.get(self.path('dashboard/')).status_code, 401)
        response = client.post('/api/v1/auth/outlet-login/', {'identifier': 'mobile-waiter', 'password': 'Mobile-test-123!'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + response.data['access'])
        self.assertEqual(client.get(self.path('dashboard/')).status_code, 200)
        meta = client.get(self.path('meta/')).data
        self.assertTrue(meta['permissions']['orders'])
        self.assertFalse(meta['permissions']['billing'])

    def test_disabled_employee_cannot_login(self):
        user = User.objects.create_user(username='disabled-mobile', password='Mobile-test-123!', role='CASHIER', branch=self.branch)
        Employee.objects.create(id='mobile-disabled', user=user, name='Disabled', email='disabled@example.com',
                                assigned_outlet=self.branch, is_active=False)
        response = APIClient().post('/api/v1/auth/outlet-login/',
                                    {'identifier': user.username, 'password': 'Mobile-test-123!'}, format='json')
        self.assertEqual(response.status_code, 403)
        self.assertNotIn('access', response.data)
