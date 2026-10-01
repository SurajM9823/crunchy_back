from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase, TransactionTestCase, override_settings
from django.core import signing
from django.core.cache import cache
from rest_framework.test import APIClient
from apps.user_accounts.models import User
from apps.restaurants.models import Restaurant, Branch
from apps.tables.models import DiningTable
from apps.catalog.models import Category, Product, ProductVariant, ModifierGroup, ModifierOption
from apps.inventory.models import InventoryItem, RecipeItem, StockTransaction
from .models import Order, OrderOutboxEvent, PosMutation, PosCreditEntry
from .tasks import publish_pos_events


@override_settings(CHANNEL_LAYERS={'default':{'BACKEND':'channels.layers.InMemoryChannelLayer'}})
class PosWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner=User.objects.create(username='pos-owner',role='RESTAURANT_OWNER')
        cls.brand=Restaurant.objects.create(name='POS brand',admin=cls.owner)
        cls.branch=Branch.objects.create(name='POS one',branch_code='POS1',restaurant=cls.brand)
        cls.other=Branch.objects.create(name='POS two',branch_code='POS2',restaurant=cls.brand)
        cls.owner.restaurant=cls.brand;cls.owner.save()
        cls.manager=User.objects.create(username='pos-manager',role='BRANCH_MANAGER',branch=cls.branch,restaurant=cls.brand)
        cls.cashier=User.objects.create(username='pos-cashier',role='CASHIER',branch=cls.branch,restaurant=cls.brand)
        cls.chef=User.objects.create(username='pos-chef',role='CHEF',branch=cls.branch,restaurant=cls.brand)
        cls.table=DiningTable.objects.create(branch=cls.branch,table_number='A1')
        cls.category=Category.objects.create(name='Food',restaurant=cls.brand)
        cls.product=Product.objects.create(name='Burger',category=cls.category,base_price=Decimal('200'))
        cls.soda=Product.objects.create(name='Soda',category=cls.category,base_price=Decimal('100'),requires_kitchen=False)
        cls.variant=ProductVariant.objects.create(product=cls.product,name='Large',price=Decimal('250'),is_default=False)
        cls.group=ModifierGroup.objects.create(product=cls.product,name='Extras')
        cls.modifier=ModifierOption.objects.create(group=cls.group,name='Cheese',price_delta=Decimal('25'))
        cls.combo=Product.objects.create(name='Meal',category=cls.category,base_price=Decimal('270'),is_combo_package=True,
            combo_discount_type='fixed_price',combo_discount_value=Decimal('270'),combo_items=[{'product_id':cls.product.pk,'quantity':1},{'product_id':cls.soda.pk,'quantity':1}])
        cls.stock=InventoryItem.objects.create(branch=cls.branch,name='Bun',sku='BUN',current_stock=Decimal('20'))
        RecipeItem.objects.create(product=cls.product,inventory_item=cls.stock,quantity_required=Decimal('1'))

    def setUp(self):
        cache.clear()
        self.client=APIClient();self.client.force_authenticate(self.manager)
        self.counter=0

    def path(self,suffix=''):
        return f'/api/v1/orders/pos/{suffix}?outlet_id={self.branch.pk}'

    def post(self,suffix,data,key=None):
        self.counter+=1
        return self.client.post(self.path(suffix),data,format='json',HTTP_IDEMPOTENCY_KEY=key or f'test-{self.counter}')

    def create(self,**extra):
        data={'items':[{'product_id':self.product.pk,'quantity':1}], 'expected_total':'200.00',**extra}
        response=self.post('',data)
        self.assertEqual(response.status_code,201,response.data)
        return response.data

    def command(self,order,action,**data):
        response=self.post(f'{order["id"]}/{action}/',{'version':order['version'],**data})
        self.assertEqual(response.status_code,200,response.data)
        return response.data

    def test_order_creation_idempotency_stock_and_outbox(self):
        data={'items':[{'product_id':self.product.pk,'quantity':2}],'expected_total':'400'}
        one=self.post('',data,'retry');two=self.post('',data,'retry')
        self.assertEqual(one.status_code,201,one.data);self.assertEqual(one.data,two.data)
        self.assertEqual(one.data['order_number'], 'POS-01')
        self.assertEqual(Order.objects.count(),1);self.assertEqual(OrderOutboxEvent.objects.count(),1)
        self.stock.refresh_from_db();self.assertEqual(self.stock.current_stock,18)
        self.assertEqual(StockTransaction.objects.count(),1)
        self.assertEqual(self.post('',{**data,'notes':'changed'},'retry').status_code,409)

    def test_price_stale_and_invalid_choices_leave_no_writes(self):
        response=self.post('',{'items':[{'product_id':self.product.pk,'quantity':1}],'expected_total':'1'})
        self.assertEqual(response.status_code,409)
        response=self.post('',{'items':[{'product_id':self.soda.pk,'variant_id':self.variant.pk,'quantity':1}],'expected_total':'100'})
        self.assertEqual(response.status_code,400);self.assertFalse(Order.objects.exists())

    def test_split_credit_collection_and_fulfillment_are_independent(self):
        order=self.create(customer_name='Real Customer',customer_phone='9800000011',payment_method='SPLIT',
            tenders=[{'method':'CASH','amount':'50'},{'method':'CREDIT','amount':'150'}])
        self.assertEqual(order['settlement'],'CREDIT');self.assertEqual(order['status'],'ACCEPTED')
        for state in ['PREPARING','READY','COMPLETED']: order=self.command(order,'transition',status=state)
        self.assertEqual(order['due_amount'],'150.00')
        order=self.command(order,'settle',tenders=[{'method':'FONEPAY','amount':'75','reference':'counter-1'}])
        self.assertEqual(order['credit_amount'],'75.00');self.assertEqual(order['due_amount'],'75.00')
        order=self.command(order,'settle',tenders=[{'method':'CASH','amount':'75'}])
        self.assertEqual(order['settlement'],'PAID');self.assertEqual(order['credit_amount'],'0.00')
        self.assertEqual(sum(PosCreditEntry.objects.values_list('amount',flat=True)),0)

    def test_paid_order_stays_in_kitchen_and_stale_edits_conflict(self):
        order=self.create(tenders=[{'method':'CASH','amount':'200'}])
        self.assertEqual(order['status'],'ACCEPTED')
        updated=self.command(order,'transition',status='PREPARING')
        response=self.post(f'{order["id"]}/transition/',{'version':order['version'],'status':'READY'})
        self.assertEqual(response.status_code,409)
        self.assertEqual(updated['items'][0]['kitchen_status'],'PREPARING')

    def test_combo_snapshot_stock_and_no_double_pricing(self):
        order=self.create(items=[{'product_id':self.combo.pk,'quantity':2}],expected_total='540')
        self.assertEqual(len(order['items']),1);self.assertEqual(len(order['items'][0]['combo_components']),2)
        self.stock.refresh_from_db();self.assertEqual(self.stock.current_stock,18)
        quote=self.post('quote/',{'items':[{'product_id':self.product.pk,'variant_id':self.variant.pk,'modifier_option_ids':[self.modifier.pk],'quantity':2}]})
        self.assertEqual(quote.data['total_payable'],'550.00')
        response=self.post('quote/',{'items':[{'product_id':self.product.pk,'quantity':1,'combo_selections':[{'product_id':self.soda.pk,'quantity':1}]}]})
        self.assertEqual(response.status_code,400)

    def test_table_occupancy_and_rounds(self):
        order=self.create(fulfillment_type='DINE_IN',table_id=self.table.pk)
        response=self.post('',{'items':[{'product_id':self.soda.pk,'quantity':1}],'expected_total':'100','fulfillment_type':'DINE_IN','table_id':self.table.pk})
        self.assertEqual(response.status_code,409)
        order=self.command(order,'append',items=[{'product_id':self.soda.pk,'quantity':2}],expected_total='400')
        self.assertEqual([r['round_number'] for r in order['items']],[1,2])
        self.assertEqual(order['total_payable'],'400.00')

    def test_tenant_permissions_private_receipts_and_legacy_bypass(self):
        order=self.create()
        self.assertEqual(self.client.get(f'/api/v1/orders/pos/?outlet_id={self.other.pk}').status_code,403)
        self.client.force_authenticate(self.chef)
        self.assertEqual(self.post(f'{order["id"]}/settle/',{'version':1,'tenders':[{'method':'CASH','amount':'200'}]}).status_code,403)
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(self.path()).status_code,[401,403])
        self.assertIn(self.client.get(f'/api/v1/orders/{order["id"]}/').status_code,[401,403])
        self.assertIn(self.client.get(self.path(f'receipts/{order["receipts"][0]["id"]}/')).status_code,[401,403])
        response=self.client.post('/api/v1/payments/webhooks/esewa/',{'order_id':order['id'],'ref_id':'forged'},format='json')
        self.assertEqual(response.status_code,403)

    def test_discounts_require_manager_and_receipt_is_immutable(self):
        order=self.create()
        self.client.force_authenticate(self.cashier)
        response=self.post(f'{order["id"]}/settle/',{'version':1,'discount_amount':'20','discount_reason':'promo','tenders':[{'method':'CASH','amount':'180'}]})
        self.assertEqual(response.status_code,403)
        self.client.force_authenticate(self.manager)
        order=self.command(order,'settle',discount_amount='20',discount_reason='Manager promotion',tenders=[{'method':'CASH','amount':'180'}])
        saved=self.client.get(self.path(f'receipts/{order["receipts"][-1]["id"]}/')).data
        order=self.command(order,'refund',amount='30',method='CASH',reference='',reason='Returned item')
        again=self.client.get(self.path(f'receipts/{order["receipts"][-2]["id"]}/')).data
        self.assertEqual(saved,again)
        self.assertEqual(order['refunded_amount'],'30.00')

    def test_overpayment_and_credit_without_contact_roll_back(self):
        for tenders in [[{'method':'CASH','amount':'201'}],[{'method':'CREDIT','amount':'200'}]]:
            r=self.post('',{'items':[{'product_id':self.product.pk,'quantity':1}],'expected_total':'200','tenders':tenders})
            self.assertEqual(r.status_code,400)
        self.assertFalse(Order.objects.exists());self.assertFalse(OrderOutboxEvent.objects.exists())
        self.stock.refresh_from_db();self.assertEqual(self.stock.current_stock,20)

    def test_stock_shortage_and_void_restore(self):
        self.stock.current_stock=1;self.stock.save()
        r=self.post('',{'items':[{'product_id':self.product.pk,'quantity':2}],'expected_total':'400'})
        self.assertEqual(r.status_code,409);self.assertFalse(Order.objects.exists())
        order=self.create(items=[{'product_id':self.product.pk,'quantity':1},{'product_id':self.soda.pk,'quantity':1}],expected_total='300')
        order=self.command(order,'void',item_id=order['items'][0]['id'],reason='Customer changed choice')
        self.assertEqual(order['total_payable'],'100.00')
        self.stock.refresh_from_db();self.assertEqual(self.stock.current_stock,1)

    def test_cancel_requires_refund_and_restores_unprepared_stock(self):
        order=self.create(tenders=[{'method':'CASH','amount':'200'}])
        r=self.post(f'{order["id"]}/transition/',{'version':order['version'],'status':'CANCELLED','reason':'Changed mind'})
        self.assertEqual(r.status_code,400)
        order=self.command(order,'refund',amount='200',method='CASH',reference='',reason='Changed mind')
        order=self.command(order,'transition',status='CANCELLED',reason='Changed mind')
        self.stock.refresh_from_db();self.assertEqual(self.stock.current_stock,20)

    def test_filters_summary_no_join_multiplication_and_unpaid_not_cash(self):
        self.create(items=[{'product_id':self.product.pk,'quantity':1},{'product_id':self.soda.pk,'quantity':2}],expected_total='400')
        r=self.client.get(self.path()+'&search=o&page_size=10')
        self.assertEqual(r.status_code,200,r.data);self.assertEqual(r.data['count'],1)
        self.assertEqual(r.data['summary']['final'],'400')
        self.assertEqual(r.data['summary']['methods'],{})
        self.assertEqual(r['Cache-Control'],'private, no-store')

    def test_outbox_retries_after_redis_failure(self):
        self.create()
        with patch('apps.orders.tasks.get_channel_layer',side_effect=RuntimeError('Redis down')):
            publish_pos_events()
        row=OrderOutboxEvent.objects.get();self.assertIsNone(row.published_at);self.assertEqual(row.attempts,1)
        from django.utils import timezone
        row.next_attempt_at=timezone.now();row.save()
        publish_pos_events();row.refresh_from_db();self.assertIsNotNone(row.published_at)

    def test_socket_ticket_is_scoped_and_signed(self):
        r=self.post('socket-ticket/',{})
        self.assertEqual(r.status_code,200)
        payload=signing.loads(r.data['ticket'],salt='staff-pos-websocket',max_age=60)
        self.assertEqual(payload,{'user_id':self.manager.pk,'branch_id':self.branch.pk})

    def test_append_keeps_ready_components_and_cancel_does_not_restock_cooked_food(self):
        order=self.create()
        order=self.command(order,'transition',status='PREPARING')
        order=self.command(order,'transition',status='READY')
        order=self.command(order,'append',items=[{'product_id':self.product.pk,'quantity':1}],expected_total='400')
        self.assertEqual([r['kitchen_status'] for r in order['items']],['READY','WAITING'])
        rejected=self.post(f'{order["id"]}/transition/',{'version':order['version'],'status':'CANCELLED','reason':'Cancel new round'})
        self.assertEqual(rejected.status_code,400)
        self.command(order,'void',item_id=order['items'][1]['id'],reason='Cancel waiting item')
        self.stock.refresh_from_db();self.assertEqual(self.stock.current_stock,19)

    def test_bill_snapshot_without_payment_and_table_session_release(self):
        order=self.create(fulfillment_type='DINE_IN',table_id=self.table.pk)
        self.table.refresh_from_db();self.assertIsNotNone(self.table.active_session_id)
        order=self.command(order,'bill')
        self.assertEqual(order['paid_amount'],'0.00');self.assertEqual(order['receipts'][-1]['kind'],'BILL')
        for state in ['PREPARING','READY','COMPLETED']:order=self.command(order,'transition',status=state)
        self.table.refresh_from_db();self.assertIsNone(self.table.active_session_id)
        self.assertEqual(order['due_amount'],'200.00')

    def test_disabled_employee_cannot_use_role_to_bypass_restrictions(self):
        from apps.user_accounts.models import Employee
        Employee.objects.create(user=self.cashier,name='Cashier',email='cashier@pos.test',phone='9800000022',
            assigned_outlet=self.branch,role='CASHIER',assigned_pages=['pos'],is_active=False)
        self.client.force_authenticate(self.cashier)
        self.assertEqual(self.client.get(self.path()).status_code,403)

    def test_running_order_keeps_its_original_billing_policy(self):
        order=self.create()
        self.brand.is_service_charge_enabled=True;self.brand.service_charge_percent=10;self.brand.save()
        order=self.command(order,'append',items=[{'product_id':self.product.pk,'quantity':1}],expected_total='400')
        self.assertEqual(order['service_charge_amount'],'0.00')

    def test_layout_groups_tables_idempotency_and_isolation(self):
        from apps.tables.models import TableGroup
        group = self.post('table-groups/', {'name': 'First floor'}, key='floor')
        self.assertEqual(group.status_code, 200, group.data)
        self.assertEqual(self.post('table-groups/', {'name': 'First floor'}, key='floor').data, group.data)
        self.assertEqual(TableGroup.objects.filter(branch=self.branch).count(), 1)
        self.assertEqual(self.post('table-groups/', {'name': 'first FLOOR'}).status_code, 400)
        data = {'table_number': 'Window A', 'capacity': 6, 'group_id': group.data['id']}
        table = self.post('tables/', data)
        self.assertEqual(table.status_code, 200, table.data)
        self.assertEqual(self.post('tables/', data).status_code, 400)
        self.assertEqual(self.post('tables/', {**data, 'table_number': 'X', 'capacity': 0}).status_code, 400)
        foreign = TableGroup.objects.create(branch=self.other, name='Foreign')
        self.assertEqual(self.post('tables/', {**data, 'group_id': foreign.pk}).status_code, 400)
        self.assertEqual(self.post(f'table-groups/{foreign.pk}/', {'name': 'Stolen'}).status_code, 404)
        response = self.post(f'table-groups/{group.data["id"]}/', {'name': 'Terrace'})
        self.assertEqual(response.status_code, 200)
        saved = DiningTable.objects.get(pk=table.data['id'])
        self.assertEqual(saved.section, 'Terrace')
        order = self.create(fulfillment_type='DINE_IN', table_id=saved.pk)
        self.assertEqual(self.post(f'tables/{saved.pk}/', {**data, 'is_active': False}).status_code, 409)
        meta = self.client.get(self.path('meta/')).data
        self.assertEqual(next(t for t in meta['tables'] if t['id'] == saved.pk)['active_order_id'], order['id'])
        self.client.force_authenticate(self.chef)
        self.assertEqual(self.post('table-groups/', {'name': 'No permission'}).status_code, 403)

    def test_quote_append_billing_contract_and_receipt_balances(self):
        self.brand.is_service_charge_enabled = True
        self.brand.service_charge_percent = Decimal('10')
        self.brand.save()
        items = [{'product_id': self.product.pk, 'quantity': 1}]
        quoted = self.post('quote/', {'items': items, 'payment_method': 'CASH'}).data
        order = self.create(expected_total=quoted['total_payable'], fulfillment_type='DINE_IN', table_id=self.table.pk)
        self.assertEqual(order['total_payable'], '220.00')
        quoted = self.post('quote/', {'items': items, 'order_id': order['id']}).data
        self.assertEqual(quoted['order_version'], order['version'])
        order = self.command(order, 'append', items=items, expected_total=quoted['total_payable'])
        bill = self.post(f'{order["id"]}/billing-quote/', {'version': order['version'], 'discount_amount': '40'})
        self.assertEqual(bill.data['total_payable'], '396.00')
        order = self.command(order, 'settle', discount_amount='40', discount_reason='Promotion',
                             tenders=[{'method': 'CASH', 'amount': '100'}, {'method': 'CARD', 'amount': '96'}])
        self.assertEqual(order['settlement'], 'PARTIAL')
        self.assertEqual(order['due_amount'], '200.00')
        bill = self.post(f'{order["id"]}/billing-quote/', {'version': order['version']})
        self.assertEqual(bill.data['due_amount'], '200.00')
        self.assertEqual(order['outlet_id'], self.branch.pk)
        order = self.command(order, 'settle', tenders=[{'method': 'CASH', 'amount': '200'}])
        self.assertEqual(order['settlement'], 'PAID')
        snap = self.client.get(self.path(f'receipts/{order["receipts"][-1]["id"]}/')).data['snapshot']
        self.assertEqual(snap['paid_amount'], '396.00')
        self.assertEqual(snap['due_amount'], '0')

    def test_empty_lists_stay_empty_and_filters_include_table_and_refunds(self):
        self.assertEqual(self.client.get(self.path() + '&open_tabs=true').data['results'], [])
        order = self.create(fulfillment_type='DINE_IN', table_id=self.table.pk, tenders=[{'method': 'CASH', 'amount': '200'}])
        self.assertEqual(self.client.get(self.path() + '&search=A1').data['count'], 1)
        order = self.command(order, 'refund', amount='30', method='CASH', reason='Returned', reference='')
        result = self.client.get(self.path() + '&settlement=REFUNDED')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.data['count'], 1)

    def test_static_menu_has_no_refresh_timer_and_pricing_boundaries_are_exact(self):
        from datetime import datetime, time
        from types import SimpleNamespace
        from zoneinfo import ZoneInfo
        from apps.catalog.pricing import next_price_change
        now = datetime(2026, 9, 28, 23, 30, tzinfo=ZoneInfo('Asia/Kathmandu'))
        self.assertIsNone(next_price_change([self.product], [], 'pos', now))
        schedule = SimpleNamespace(is_active=True, channels=['pos'], days=['Mon'], start_time=time(22), end_time=time(2))
        expected = datetime(2026, 9, 29, 2, tzinfo=ZoneInfo('Asia/Kathmandu')).timestamp()
        self.assertEqual(next_price_change([self.product], [schedule], 'pos', now), expected)
        self.assertIsNone(next_price_change([self.product], [schedule], 'web', now))


@override_settings(CHANNEL_LAYERS={'default':{'BACKEND':'channels.layers.InMemoryChannelLayer'}})
class PosSocketTests(TransactionTestCase):
    def setUp(self):
        self.user=User.objects.create(username='socket-owner',role='RESTAURANT_OWNER')
        self.brand=Restaurant.objects.create(name='Socket brand',admin=self.user)
        self.branch=Branch.objects.create(name='Socket branch',branch_code='SOCKET',restaurant=self.brand)
        self.user.restaurant=self.brand;self.user.save()

    def test_authenticated_socket_heartbeat_and_scope_rejection(self):
        from asgiref.sync import async_to_sync
        from channels.routing import URLRouter
        from channels.testing import WebsocketCommunicator
        from .routing import websocket_urlpatterns
        ticket=signing.dumps({'user_id':self.user.pk,'branch_id':self.branch.pk},salt='staff-pos-websocket')
        async def scenario():
            app=URLRouter(websocket_urlpatterns)
            anonymous=WebsocketCommunicator(app,f'/ws/pos/{self.branch.pk}/')
            connected,_=await anonymous.connect();self.assertFalse(connected);await anonymous.disconnect()
            wrong=WebsocketCommunicator(app,f'/ws/pos/{self.branch.pk+1}/?ticket={ticket}')
            connected,_=await wrong.connect();self.assertFalse(connected);await wrong.disconnect()
            socket=WebsocketCommunicator(app,f'/ws/pos/{self.branch.pk}/?ticket={ticket}')
            connected,_=await socket.connect();self.assertTrue(connected)
            self.assertEqual((await socket.receive_json_from())['event_type'],'CONNECTED')
            await socket.send_json_to({'type':'ping'})
            self.assertEqual((await socket.receive_json_from())['event_type'],'HEARTBEAT')
            await socket.disconnect()
        async_to_sync(scenario)()
