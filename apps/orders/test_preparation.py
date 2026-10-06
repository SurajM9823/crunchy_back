from django.test import TestCase
from rest_framework.test import APIClient
from apps.tables.qr_security import generate_table_qr_token
from .test_pos import PosWorkflowTests
from .models import Order, OrderOutboxEvent
from .receipts import tracking_token


class PreparationRoundTests(TestCase):
    setUpTestData = classmethod(PosWorkflowTests.setUpTestData.__func__)
    setUp = PosWorkflowTests.setUp
    path = PosWorkflowTests.path
    post = PosWorkflowTests.post
    create = PosWorkflowTests.create
    command = PosWorkflowTests.command

    def test_two_rounds_keep_timers_and_finish_independently(self):
        order = self.create(fulfillment_type='DINE_IN', table_id=self.table.pk)
        order = self.command(order, 'round', round_number=1, status='PREPARING')
        started = order['rounds'][0]['preparation_started_at']
        order = self.command(order, 'append', items=[{'product_id':self.product.pk,'quantity':1}], expected_total='400')
        self.assertEqual(order['status'], 'PREPARING')
        self.assertEqual(order['rounds'][0]['preparation_started_at'], started)
        self.assertIsNone(order['rounds'][1]['preparation_started_at'])
        for action, fields in [('void', {'item_id':order['items'][0]['id'],'quantity':1,'reason':'reduce'}),
                               ('transition', {'status':'CANCELLED','reason':'cancel'})]:
            rejected = self.post(f'{order["id"]}/{action}/', {'version':order['version'], **fields})
            self.assertEqual(rejected.status_code, 400, rejected.data)
        order = self.command(order, 'round', round_number=1, status='READY')
        self.assertTrue(order['partial_ready'])
        order = self.command(order, 'call', round_number=1)
        self.assertEqual(OrderOutboxEvent.objects.filter(event_type='ORDER_CALL').get().payload['round_number'], 1)
        display = self.client.get(f'/api/v1/orders/display/{self.branch.pk}/').data
        self.assertIn(order['order_number'], display['ready'])
        self.assertIn(order['order_number'], display['preparing'])
        order = self.command(order, 'round', round_number=1, status='SERVED')
        self.assertNotEqual(order['status'], 'COMPLETED')
        self.table.refresh_from_db()
        self.assertIsNotNone(self.table.active_session_id)
        for state in ['PREPARING','READY','SERVED']:
            order = self.command(order, 'round', round_number=2, status=state)
        self.assertEqual(order['status'], 'COMPLETED')
        self.assertFalse(order['can_append'])
        self.table.refresh_from_db()
        self.assertIsNone(self.table.active_session_id)

    def test_waiting_quantity_removal_restores_stock_once_and_rejects_stale_edits(self):
        order = self.create(items=[{'product_id':self.product.pk,'quantity':3}], expected_total='600')
        payload = {'version':order['version'],'item_id':order['items'][0]['id'],'quantity':1,'reason':'one fewer'}
        first = self.post(f'{order["id"]}/void/', payload, 'reduce-once')
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(first.data['items'][0]['quantity'], 2)
        self.assertEqual(first.data['total_payable'], '400.00')
        self.assertEqual(self.post(f'{order["id"]}/void/', payload, 'reduce-once').data, first.data)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.current_stock, 18)
        self.assertEqual(self.post(f'{order["id"]}/void/', payload).status_code, 409)
        cooking = self.command(first.data, 'round', round_number=1, status='PREPARING')
        self.assertEqual(self.post(f'{order["id"]}/void/', {**payload,'version':cooking['version']}).status_code, 400)

    def test_removal_rebuilds_totals_for_billing_and_receipts(self):
        order = self.create(items=[{'product_id': self.product.pk, 'quantity': 4}], expected_total='800')
        # Recover from an inconsistent stored subtotal using the surviving lines.
        Order.objects.filter(pk=order['id']).update(subtotal='1000', total_payable='1000')
        reduced = self.command(order, 'void', item_id=order['items'][0]['id'], quantity=2, reason='Two fewer')
        self.assertEqual(reduced['subtotal'], '400.00')
        self.assertEqual(reduced['total_payable'], '400.00')
        self.assertEqual(reduced['due_amount'], '400.00')
        detail = self.client.get(self.path(f'{order["id"]}/')).data
        self.assertEqual(detail['items'][0]['quantity'], 2)
        self.assertEqual(detail['items'][0]['line_total'], '400.00')
        bill = self.post(f'{order["id"]}/billing-quote/', {'version': reduced['version']})
        self.assertEqual(bill.status_code, 200, bill.data)
        self.assertEqual(bill.data['due_amount'], '400.00')
        receipt = Order.objects.get(pk=order['id']).pos_receipts.order_by('-pk').first()
        self.assertEqual(receipt.snapshot['subtotal'], '400.00')

    def test_delivery_requires_all_rounds_and_cannot_append_in_transit(self):
        order = self.create(fulfillment_type='DELIVERY', customer_phone='9800000000', delivery_address='Door')
        for state in ['PREPARING','READY']:
            order = self.command(order, 'round', round_number=1, status=state)
        order = self.command(order, 'append', items=[{'product_id':self.product.pk,'quantity':1}], expected_total='400')
        self.assertEqual(self.post(f'{order["id"]}/transition/', {'version':order['version'],'status':'OUT_FOR_DELIVERY'}).status_code, 400)
        self.assertEqual(self.post(f'{order["id"]}/round/', {'version':order['version'],'round_number':1,'status':'SERVED'}).status_code, 400)
        for state in ['PREPARING','READY']:
            order = self.command(order, 'round', round_number=2, status=state)
        order = self.command(order, 'transition', status='OUT_FOR_DELIVERY')
        self.assertFalse(order['can_append'])
        self.assertEqual(self.post(f'{order["id"]}/append/', {'version':order['version'],'items':[{'product_id':self.product.pk,'quantity':1}],'expected_total':'600'}).status_code, 400)

    def test_confirmed_web_order_is_immutable(self):
        order = self.create()
        Order.objects.filter(pk=order['id']).update(order_source='WEBSITE')
        for action, fields in [('append', {'items':[{'product_id':self.product.pk,'quantity':1}],'expected_total':'400'}),
                               ('void', {'item_id':order['items'][0]['id'],'reason':'remove'})]:
            self.assertEqual(self.post(f'{order["id"]}/{action}/', {'version':order['version'],**fields}).status_code, 400)

    def test_qr_mutations_require_table_and_order_capabilities_and_current_version(self):
        guest = APIClient()
        qr = generate_table_qr_token(self.table)
        body = {'branch_id':self.branch.pk,'order_source':'TABLE_QR','fulfillment_type':'DINE_IN',
                'qr_token':qr,'items':[{'product_id':self.product.pk,'quantity':3}],'expected_total':'600'}
        created = guest.post('/api/v1/orders/self-service/checkout/',body,format='json',HTTP_IDEMPOTENCY_KEY='qr-create')
        self.assertEqual(created.status_code,201,created.data)
        order = created.data
        payload = {'tracking_token':order['tracking_token'],'qr_token':qr,'version':order['version'],
                   'item_id':order['items'][0]['id'],'quantity':1}
        url = '/api/v1/orders/self-service/items/void/'
        def remove(data, key):
            return guest.post(url,data,format='json',HTTP_IDEMPOTENCY_KEY=key)
        self.assertEqual(remove({**payload,'tracking_token':tracking_token(order['id'])},'public').status_code,404)
        self.assertEqual(remove({**payload,'qr_token':'bad'},'bad-table').status_code,404)
        reduced = remove(payload,'remove-once')
        self.assertEqual(reduced.status_code,200,reduced.data)
        self.assertEqual(remove(payload,'remove-once').data,reduced.data)
        self.assertEqual(remove(payload,'stale').status_code,409)
        cooking = self.command(reduced.data,'round',round_number=1,status='PREPARING')
        self.assertEqual(remove({**payload,'version':cooking['version']},'cooking').status_code,400)
        appended = guest.post('/api/v1/orders/self-service/checkout/',{**body,'tracking_token':order['tracking_token'],'version':cooking['version']},format='json',HTTP_IDEMPOTENCY_KEY='new-round')
        self.assertEqual(appended.status_code,201,appended.data)
        self.assertEqual([r['status'] for r in appended.data['rounds']],['PREPARING','WAITING'])
