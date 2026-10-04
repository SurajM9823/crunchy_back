from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
from unittest.mock import AsyncMock, patch
from asgiref.sync import async_to_sync
from django.core import signing
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from apps.orders import test_pos
from apps.orders.models import Order
from apps.payments.models import PaymentTransaction
from apps.user_accounts.models import Employee
from .models import DaybookEntry, DaybookEvent
from .serializers import today
from .tasks import publish_daybook_events


@override_settings(CHANNEL_LAYERS={'default': {'BACKEND':'channels.layers.InMemoryChannelLayer'}})
class DaybookTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        test_pos.PosWorkflowTests.setUpTestData.__func__(cls)

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.manager)
        self.counter = 0
        self.order = Order.objects.create(branch=self.branch, order_number='DAYBOOK-ORDER', is_pos_managed=True,
            subtotal=200, total_payable=200, customer_name='Real customer', order_source='POS')

    def path(self, suffix='', branch=None):
        return f'/api/v1/daybook/{suffix}?outlet_id={(branch or self.branch).pk}'

    def post(self, suffix='', data=None, key=None, branch=None):
        self.counter += 1
        return self.client.post(self.path(suffix, branch), data or {}, format='json',
            HTTP_IDEMPOTENCY_KEY=key or f'request-{self.counter}')

    def entry(self, **changes):
        payload = {'date':today().isoformat(), 'direction':'IN', 'amount':'100.00', 'payment_method':'CASH',
                   'category':'Capital deposit', 'description':'Starting float', **changes}
        result = self.post(data=payload)
        self.assertEqual(result.status_code, 201, result.data)
        return result.data

    def payment(self, amount='100', method='CASH', status='SUCCESS', branch=None, created_at=None):
        self.counter += 1
        branch = branch or self.branch
        order = self.order if branch == self.branch else Order.objects.create(branch=branch, order_number=f'OTHER-{self.counter}')
        result = PaymentTransaction.objects.create(order=order, branch=branch, transaction_id=f'TXN-{self.counter}',
            amount=amount, payment_method=method, status=status)
        if created_at:
            PaymentTransaction.objects.filter(pk=result.pk).update(created_at=created_at)
        return result

    def preview(self, **params):
        return self.client.get(self.path('import/'), {'date':today().isoformat(), **params})

    def import_payments(self, ids, **extra):
        return self.post('import/', {'date':today().isoformat(), 'kind':'SALE', 'payment_ids':ids, **extra})

    def test_empty_ledger_and_in_out_cash_and_opening_balances(self):
        empty = self.client.get(self.path()).data
        self.assertEqual(empty['results'], [])
        self.assertEqual(empty['summary']['opening'], '0.00')
        self.entry(date=(today()-timedelta(days=1)).isoformat(), amount='500')
        self.entry(amount='200')
        self.entry(amount='300', payment_method='FONEPAY')
        self.entry(direction='OUT', amount='900', category='Cash withdrawal', party='Manager')
        result = self.client.get(self.path()).data
        self.assertEqual(result['summary'], {'opening':'500.00','income':'500.00','expense':'900.00',
            'net':'-400.00','closing':'100.00','cash_opening':'500.00','cash_in':'200.00',
            'cash_out':'900.00','cash_closing':'-200.00'})
        filtered = self.client.get(self.path(), {'search':'Manager','direction':'OUT'}).data
        self.assertEqual(filtered['count'], 1)
        self.assertEqual(filtered['summary'], result['summary'])

    def test_manual_idempotency_validation_and_actor_cannot_be_forged(self):
        data = {'direction':'OUT','amount':'75.50','category':'Cash withdrawal','description':'Cash taken out',
                'recorded_by':self.owner.pk, 'branch':self.other.pk}
        one = self.post(data=data, key='same')
        two = self.post(data=data, key='same')
        self.assertEqual(one.status_code, 201, one.data)
        self.assertEqual(one.data, two.data)
        self.assertEqual(DaybookEntry.objects.count(), 1)
        self.assertEqual(DaybookEntry.objects.get().recorded_by_id, self.manager.pk)
        self.assertEqual(DaybookEntry.objects.get().branch_id, self.branch.pk)
        self.assertEqual(self.post(data={**data,'amount':'76'}, key='same').status_code, 409)
        for changes in [{'amount':'0'}, {'amount':'-1'}, {'amount':'NaN'}, {'direction':'BAD'},
                        {'payment_method':'CREDIT'}, {'date':(today()+timedelta(days=1)).isoformat()}, {'description':''}]:
            result = self.post(data={**data, **changes})
            self.assertEqual(result.status_code, 400, result.data)

    def test_import_selection_split_payments_and_no_double_count(self):
        cash = self.payment('80')
        digital = self.payment('120', 'FONEPAY')
        self.payment('999', status='PENDING')
        self.payment('888', status='FAILED')
        self.payment('777', method='CREDIT')
        self.payment('55', branch=self.other)
        preview = self.preview().data
        self.assertEqual({p['id'] for p in preview['results']}, {cash.pk,digital.pk})
        self.assertTrue(all(not p['imported'] for p in preview['results']))
        first = self.import_payments([cash.pk])
        self.assertEqual(first.data['created'], 1)
        self.assertEqual(DaybookEntry.objects.get().amount, Decimal('80'))
        second = self.import_payments([cash.pk, digital.pk])
        self.assertEqual(second.data['created'], 1)
        self.assertEqual(second.data['skipped'], 1)
        self.assertEqual(self.client.get(self.path()).data['summary']['income'], '200.00')
        self.assertTrue(all(p['imported'] for p in self.preview().data['results']))

    def test_import_uses_server_amount_and_rejects_wrong_outlet_and_date_atomically(self):
        valid = self.payment('125')
        foreign = self.payment('500', branch=self.other)
        old = self.payment('100', created_at=timezone.now()-timedelta(days=2))
        for ids in [[valid.pk, foreign.pk], [valid.pk, old.pk], [999999]]:
            self.assertEqual(self.import_payments(ids).status_code, 409)
            self.assertEqual(DaybookEntry.objects.count(), 0)
        response = self.import_payments([valid.pk, valid.pk], amount='1', direction='OUT')
        self.assertEqual(response.data['created'], 1)
        row = DaybookEntry.objects.get()
        self.assertEqual(row.direction, 'IN')
        self.assertEqual(row.amount, Decimal('125'))

    def test_import_uses_nepal_payment_day_not_order_creation_day(self):
        start = datetime.combine(today(), datetime.min.time(), ZoneInfo('Asia/Kathmandu'))
        Order.objects.filter(pk=self.order.pk).update(created_at=start-timedelta(days=5))
        before = self.payment(created_at=start-timedelta(seconds=1))
        at_start = self.payment(created_at=start)
        last = self.payment(created_at=start+timedelta(days=1, seconds=-1))
        tomorrow = self.payment(created_at=start+timedelta(days=1))
        self.assertEqual({p['id'] for p in self.preview().data['results']}, {at_start.pk, last.pk})

    def test_refunds_are_separate_out_entries_and_original_sales_remain(self):
        payment = self.payment('200')
        refund = self.payment('50', status='REFUNDED')
        self.assertEqual(self.import_payments([payment.pk]).data['created'], 1)
        self.assertEqual({p['id'] for p in self.preview(kind='REFUND').data['results']}, {refund.pk})
        self.assertEqual(self.import_payments([refund.pk], kind='REFUND').data['created'], 1)
        result = self.client.get(self.path()).data
        self.assertEqual(result['summary']['income'], '200.00')
        self.assertEqual(result['summary']['expense'], '50.00')
        self.assertEqual(result['summary']['closing'], '150.00')

    def test_void_preserves_audit_and_does_not_allow_reimport(self):
        payment = self.payment('200')
        self.import_payments([payment.pk])
        row = DaybookEntry.objects.get()
        self.assertEqual(self.post(f'{row.pk}/void/', {}).status_code, 400)
        voided = self.post(f'{row.pk}/void/', {'reason':'Recorded twice manually'})
        self.assertEqual(voided.status_code, 200, voided.data)
        self.assertEqual(self.client.get(self.path()).data['count'], 0)
        audit = self.client.get(self.path(), {'include_voided':'true'}).data
        self.assertEqual(audit['results'][0]['void_reason'], 'Recorded twice manually')
        self.assertEqual(audit['summary']['income'], '0.00')
        self.assertEqual(self.import_payments([payment.pk]).data['created'], 0)
        payment.refresh_from_db()
        self.assertEqual(payment.status, 'SUCCESS')

    def test_permissions_and_staff_daybook_page_access(self):
        row = self.entry()
        self.client.force_authenticate(self.cashier)
        self.assertEqual(self.client.get(self.path()).status_code, 200)
        self.assertFalse(self.client.get(self.path()).data['can_void'])
        self.assertEqual(self.post(f'{row["id"]}/void/', {'reason':'No permission'}).status_code, 403)
        self.assertEqual(self.client.get(self.path(branch=self.other)).status_code, 403)
        self.assertEqual(self.post(data={}, branch=self.other).status_code, 403)
        self.client.force_authenticate(self.chef)
        self.assertEqual(self.client.get(self.path()).status_code, 403)
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(self.path()).status_code, [401,403])

    def test_employee_with_daybook_only_can_record_but_not_void(self):
        Employee.objects.create(id='daybook-cashier', user=self.cashier, name='Cashier',
            email='daybook@example.test', phone='9800000000', role='CASHIER',
            assigned_outlet=self.branch, assigned_pages=['daybook'])
        self.client.force_authenticate(self.cashier)
        row = self.entry()
        self.assertEqual(self.client.get(self.path()).status_code, 200)
        self.assertEqual(self.post(f'{row["id"]}/void/', {'reason':'No permission'}).status_code, 403)

    def test_pagination_search_and_outlet_isolation(self):
        for n in range(12):
            self.entry(description=f'Entry number {n}')
        DaybookEntry.objects.create(branch=self.other, date=today(), direction='IN', amount='999',
            payment_method='CASH', category='Other', description='Private other outlet', recorded_by=self.owner)
        result = self.client.get(self.path(), {'page_size':10,'page':2}).data
        self.assertEqual(result['count'], 12)
        self.assertEqual(len(result['results']), 2)
        self.assertEqual(self.client.get(self.path(), {'search':'number 11'}).data['count'], 1)

    def test_outbox_event_retries_without_changing_saved_entries(self):
        self.entry()
        with patch('apps.daybook.tasks.get_channel_layer', side_effect=RuntimeError('Redis unavailable')):
            publish_daybook_events()
        event = DaybookEvent.objects.get()
        self.assertIsNone(event.published_at)
        self.assertEqual(event.attempts, 1)
        event.next_attempt_at = timezone.now()
        event.save()
        with patch('apps.daybook.tasks.get_channel_layer') as layer:
            layer.return_value.group_send = AsyncMock()
            publish_daybook_events()
            group, payload = layer.return_value.group_send.call_args.args
            self.assertEqual(group, f'daybook_{self.branch.pk}')
            self.assertNotIn('amount', payload)
        event.refresh_from_db()
        self.assertIsNotNone(event.published_at)
        self.assertEqual(DaybookEntry.objects.count(), 1)


@override_settings(CHANNEL_LAYERS={'default': {'BACKEND':'channels.layers.InMemoryChannelLayer'}})
class DaybookSocketTests(TransactionTestCase):
    def setUp(self):
        test_pos.PosWorkflowTests.setUpTestData.__func__(type(self))

    def test_socket_authorization_heartbeat_and_live_event(self):
        from channels.routing import URLRouter
        from channels.testing import WebsocketCommunicator
        from channels.layers import get_channel_layer
        from .routing import websocket_urlpatterns
        ticket = signing.dumps({'user_id':self.manager.pk,'branch_id':self.branch.pk}, salt='daybook-socket')

        async def run():
            application = URLRouter(websocket_urlpatterns)
            anonymous = WebsocketCommunicator(application, f'/ws/daybook/{self.branch.pk}/')
            self.assertFalse((await anonymous.connect())[0])
            wrong = WebsocketCommunicator(application, f'/ws/daybook/{self.other.pk}/?ticket={ticket}')
            self.assertFalse((await wrong.connect())[0])
            socket = WebsocketCommunicator(application, f'/ws/daybook/{self.branch.pk}/?ticket={ticket}')
            self.assertTrue((await socket.connect())[0])
            await socket.send_json_to({'type':'ping'})
            self.assertEqual((await socket.receive_json_from())['event_type'], 'HEARTBEAT')
            await get_channel_layer().group_send(f'daybook_{self.branch.pk}', {'type':'daybook_event', 'event_type':'DAYBOOK_CREATE'})
            self.assertEqual((await socket.receive_json_from())['event_type'], 'DAYBOOK_CREATE')
            await socket.disconnect()

        async_to_sync(run)()
