from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase, override_settings

from rest_framework.test import APIClient

from . import test_pos
from .models import MobilePushDevice, Order, OrderOutboxEvent, OrderPushDelivery
from .tasks import send_order_push_notifications
from apps.payments.models import PaymentTransaction
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

    def test_dashboard_summary_includes_tender_and_credit_refund_totals(self):
        order = self.create()
        Order.objects.filter(pk=order['id']).update(
            paid_amount=Decimal('100.00'),
            credit_amount=Decimal('20.00'),
            refunded_amount=Decimal('5.00'),
        )
        for method, amount in (
            ('CASH', '40.00'),
            ('FONEPAY', '30.00'),
            ('ESEWA', '20.00'),
            ('CARD', '10.00'),
        ):
            PaymentTransaction.objects.create(
                transaction_id=f'SUMMARY-{method}',
                order_id=order['id'],
                branch=self.branch,
                amount=Decimal(amount),
                payment_method=method,
                status='SUCCESS',
            )

        response = self.client.get(self.path('dashboard/'))

        self.assertEqual(response.status_code, 200)
        summary = response.data['summary']
        self.assertEqual(
            {method: Decimal(amount) for method, amount in summary['methods'].items()},
            {
                'CASH': Decimal('40.00'),
                'FONEPAY': Decimal('30.00'),
                'ESEWA': Decimal('20.00'),
                'CARD': Decimal('10.00'),
            },
        )
        self.assertEqual(Decimal(summary['credit']), Decimal('20.00'))
        self.assertEqual(summary['credit_count'], 1)
        self.assertEqual(Decimal(summary['refunded']), Decimal('5.00'))
        self.assertEqual(summary['refund_void_count'], 1)

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

    def test_push_device_registration_is_authenticated_and_outlet_scoped(self):
        endpoint = self.path('notifications/device/')
        self.assertEqual(APIClient().post(endpoint, {'token': 'device-token'}, format='json').status_code, 401)

        response = self.client.post(endpoint, {'token': 'device-token'}, format='json')
        self.assertEqual(response.status_code, 200)
        device = MobilePushDevice.objects.get(token='device-token')
        self.assertEqual(device.user, self.manager)
        self.assertEqual(device.branch, self.branch)

        other_outlet_endpoint = f'/api/v1/orders/pos/notifications/device/?outlet_id={self.other.pk}'
        self.assertEqual(
            self.client.post(other_outlet_endpoint, {'token': 'other-token'}, format='json').status_code,
            403,
        )
        self.assertFalse(MobilePushDevice.objects.filter(token='other-token').exists())

        response = self.client.delete(
            endpoint,
            {'token': 'device-token'},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        device.refresh_from_db()
        self.assertFalse(device.active)

    @patch('apps.orders.push_notifications.send_new_order_push')
    def test_new_order_push_is_sent_to_authorized_devices(self, send_push):
        self.create()
        MobilePushDevice.objects.create(
            user=self.manager,
            branch=self.branch,
            token='authorized-token',
        )

        send_order_push_notifications()

        send_push.assert_called_once()
        delivery = OrderPushDelivery.objects.get(device__token='authorized-token')
        self.assertIsNotNone(delivery.sent_at)
        self.assertEqual(delivery.attempts, 1)

    @patch('apps.orders.push_notifications.send_new_order_push')
    def test_queued_push_is_skipped_after_device_is_reassigned(self, send_push):
        order_data = self.create()
        event = OrderOutboxEvent.objects.get(order_id=order_data['id'])
        device = MobilePushDevice.objects.create(
            user=self.manager,
            branch=self.branch,
            token='reassigned-token',
        )
        delivery = OrderPushDelivery.objects.create(event=event, device=device, user=self.manager)
        device.branch = self.other
        device.save(update_fields=['branch'])

        send_order_push_notifications()

        send_push.assert_not_called()
        delivery.refresh_from_db()
        self.assertIsNotNone(delivery.sent_at)
        self.assertIn('no longer authorized', delivery.last_error)

    @patch('apps.orders.push_notifications.send_new_order_push', side_effect=[RuntimeError('temporary'), None])
    def test_new_order_push_retries_transient_firebase_failure(self, send_push):
        self.create()
        MobilePushDevice.objects.create(
            user=self.manager,
            branch=self.branch,
            token='retry-token',
        )

        send_order_push_notifications()
        delivery = OrderPushDelivery.objects.get(device__token='retry-token')
        self.assertIsNone(delivery.sent_at)
        self.assertEqual(delivery.attempts, 1)
        self.assertGreater(delivery.next_attempt_at, datetime.now(timezone.utc))

        delivery.next_attempt_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
        delivery.save(update_fields=['next_attempt_at'])
        send_order_push_notifications()

        delivery.refresh_from_db()
        self.assertIsNotNone(delivery.sent_at)
        self.assertEqual(delivery.attempts, 2)
        self.assertEqual(send_push.call_count, 2)
