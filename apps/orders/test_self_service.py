from unittest.mock import patch
from django.test import TestCase, override_settings
from django.core.cache import cache
from rest_framework.test import APIClient
from apps.tables.qr_security import generate_table_qr_token
from apps.inventory.models import InventoryItem
from .test_pos import PosWorkflowTests
from .models import Order, OrderOutboxEvent
from .tasks import publish_pos_events


@override_settings(CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class SelfServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        PosWorkflowTests.setUpTestData.__func__(cls)
        cls.branch.enable_kiosk = True
        cls.branch.save(update_fields=['enable_kiosk'])

    def setUp(self):
        cache.clear()
        self.client = APIClient()

    def body(self, **changes):
        return {'branch_id': self.branch.pk, 'order_source': 'KIOSK', 'fulfillment_type': 'TAKEAWAY',
                'customer_name': 'Guest name', 'customer_phone': '9800000000', 'expected_total': '200.00',
                'items': [{'product_id': self.product.pk, 'quantity': 1}], **changes}

    def checkout(self, body=None, key='guest-request'):
        return self.client.post('/api/v1/orders/self-service/checkout/', body or self.body(), format='json', HTTP_IDEMPOTENCY_KEY=key)

    def test_kiosk_persists_contact_stock_and_exactly_once_order(self):
        first = self.checkout()
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(first.data['order_number'], 'K-01')
        again = self.checkout()
        self.assertEqual(first.data, again.data)
        order = Order.objects.get()
        self.assertTrue(order.is_pos_managed)
        self.assertEqual(order.customer_phone, '9800000000')
        self.assertEqual(order.customer_name, 'Guest name')
        self.assertEqual(order.paid_amount, 0)
        self.assertEqual(InventoryItem.objects.get(pk=self.stock.pk).current_stock, 19)
        self.assertEqual(OrderOutboxEvent.objects.count(), 1)
        self.client.force_authenticate(self.manager)
        orders = self.client.get(f'/api/v1/orders/pos/?outlet_id={self.branch.pk}').data
        self.assertEqual(orders['results'][0]['id'], order.pk)

    def test_signed_qr_rounds_share_staff_tab_and_retry_does_not_append_twice(self):
        body = self.body(order_source='TABLE_QR', fulfillment_type='DINE_IN', qr_token=generate_table_qr_token(self.table))
        first = self.checkout(body)
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(first.data['order_number'], 'QR-01')
        second = self.checkout(body, key='round-two')
        self.assertEqual(second.status_code, 201, second.data)
        self.assertEqual(first.data['id'], second.data['id'])
        self.assertEqual(second.data['total_payable'], '400.00')
        self.assertEqual(self.checkout(body, key='round-two').data, second.data)
        self.assertEqual(Order.objects.get().items.count(), 2)
        self.assertEqual(self.checkout(self.body(fulfillment_type='DINE_IN', table_id=self.table.pk), 'kiosk-other').status_code, 409)

    def test_invalid_qr_outlet_and_key_are_rejected(self):
        self.assertEqual(self.checkout(self.body(order_source='TABLE_QR', fulfillment_type='DINE_IN', qr_token='bad')).status_code, 400)
        self.assertEqual(self.checkout(self.body(order_source='TABLE_QR', fulfillment_type='DINE_IN', branch_id=self.other.pk, qr_token=generate_table_qr_token(self.table))).status_code, 400)
        self.assertEqual(self.checkout().status_code, 201)
        self.assertEqual(self.checkout(self.body(customer_name='Changed')).status_code, 409)

    def test_tracking_requires_signed_link_and_follows_staff_status(self):
        created = self.checkout().data
        self.assertEqual(self.client.get('/api/v1/orders/self-service/order/?token=bad').status_code, 404)
        self.client.force_authenticate(self.manager)
        response = self.client.post(f'/api/v1/orders/pos/{created["id"]}/transition/?outlet_id={self.branch.pk}',
            {'version': 1, 'status': 'PREPARING'}, format='json', HTTP_IDEMPOTENCY_KEY='prepare')
        self.assertEqual(response.status_code, 200, response.data)
        self.client.force_authenticate(None)
        tracked = self.client.get('/api/v1/orders/self-service/order/', {'token': created['tracking_token']})
        self.assertEqual(tracked.data['status'], 'PREPARING')
        display = self.client.get(f'/api/v1/orders/display/{self.branch.pk}/').data
        self.assertIn(created['order_number'], display['preparing'])
        self.assertNotIn('customer_phone', display['tickets'][0])

    def test_calls_and_transitions_publish_to_all_live_screens(self):
        created = self.checkout().data
        self.client.force_authenticate(self.manager)
        for version, state in [(1, 'PREPARING'), (2, 'READY')]:
            prepared = self.client.post(f'/api/v1/orders/pos/{created["id"]}/round/?outlet_id={self.branch.pk}',
                {'version':version,'round_number':1,'status':state},format='json',HTTP_IDEMPOTENCY_KEY=f'prepare-{version}')
            self.assertEqual(prepared.status_code,200,prepared.data)
        response = self.client.post(f'/api/v1/orders/pos/{created["id"]}/call/?outlet_id={self.branch.pk}',
            {'version': 3}, format='json', HTTP_IDEMPOTENCY_KEY='call-once')
        self.assertEqual(response.status_code, 200, response.data)
        with patch('apps.orders.tasks.get_channel_layer') as layer:
            from unittest.mock import AsyncMock
            layer.return_value.group_send = AsyncMock()
            publish_pos_events()
            calls = layer.return_value.group_send.call_args_list
            groups = {args.args[0] for args in calls}
            self.assertTrue({f'pos_{self.branch.pk}', f'outlet_{self.branch.pk}_display', f'outlet_{self.branch.pk}_kitchen', f'order_{created["id"]}'}.issubset(groups))
            event = next(args.args[1] for args in calls if args.args[0].endswith('_display') and args.args[1]['event_type']=='ORDER_CALL')
            self.assertEqual(event['order_number'], created['order_number'])
            self.assertNotIn('customer_phone', event)

    def test_table_qr_generation_is_outlet_scoped_and_resolves(self):
        self.client.force_authenticate(self.manager)
        response = self.client.get(f'/api/v1/tables/{self.table.pk}/qr/?outlet_id={self.branch.pk}')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['image'].startswith('data:image/svg+xml;base64,'))
        from urllib.parse import urlparse, parse_qs
        query = parse_qs(urlparse(response.data['url']).query)
        self.client.force_authenticate(None)
        resolved = self.client.get('/api/v1/tables/qr/resolve/', {'token': query['token'][0]})
        self.assertEqual(resolved.data['table_id'], self.table.pk)
        self.client.force_authenticate(self.manager)
        self.assertEqual(self.client.get(f'/api/v1/tables/{self.table.pk}/qr/?outlet_id={self.other.pk}').status_code, 403)
