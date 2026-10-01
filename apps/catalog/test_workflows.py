from datetime import datetime, time
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo
from django.core.cache import cache
from django.db import transaction
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from apps.user_accounts.models import User, UserRole
from apps.restaurants.models import Restaurant, Branch
from .models import Category, Product, MenuOutboxEvent, MenuRevision
from .services import category_create, product_create, product_update, schedule_save, outlet_update_product
from .selectors import get_outlet_menu, quote_items
from .pricing import active_window


class MenuWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create(username='menu-owner', role=UserRole.RESTAURANT_OWNER)
        cls.brand = Restaurant.objects.create(name='Menu Test', admin=cls.owner)
        cls.branch = Branch.objects.create(name='One', branch_code='M-1', restaurant=cls.brand)
        cls.other_branch = Branch.objects.create(name='Two', branch_code='M-2', restaurant=cls.brand)
        cls.owner.restaurant = cls.brand
        cls.owner.save()
        cls.manager = User.objects.create(username='menu-manager', role=UserRole.BRANCH_MANAGER, branch=cls.branch, restaurant=cls.brand, is_staff=True)
        cls.other_owner = User.objects.create(username='other-owner', role=UserRole.RESTAURANT_OWNER)
        cls.other_brand = Restaurant.objects.create(name='Other Test', admin=cls.other_owner)
        cls.foreign_branch = Branch.objects.create(name='Foreign', branch_code='F-1', restaurant=cls.other_brand)
        cls.category = category_create(name='Burgers', restaurant=cls.brand)
        cls.product = product_create(category=cls.category, name='Burger', base_price=Decimal('400'), variants=[{'name':'Regular', 'price':Decimal('400'), 'is_default':True}])
        cls.foreign_category = category_create(name='Burgers', restaurant=cls.other_brand)
        cls.foreign = product_create(category=cls.foreign_category, name='Secret Burger', base_price=Decimal('999'))

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.client.force_authenticate(self.manager)

    def path(self, suffix):
        return f'/api/v1/catalog/{suffix}?outlet_id={self.branch.pk}'

    def test_nested_product_and_atomic_failure(self):
        payload = {'category': self.category.pk, 'name': 'Meal', 'base_price': '100', 'main_image_index':0,
            'variants':[{'id':'size-a', 'name':'Regular', 'price':'100', 'is_default':True}],
            'modifier_groups':[{'id':'group-a', 'name':'Sauce', 'required':True, 'min_selections':1, 'max_selections':2,
                'options':[{'id':'option-a', 'name':'Hot', 'price_delta':'20'},
                    {'id':'option-b', 'name':'Garlic', 'price_delta':'15'},
                    {'id':'option-c', 'name':'Chili', 'price_delta':'10'}]}]}
        res = self.client.post(self.path('products/'), payload, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        pid = res.data['id']
        self.assertEqual(res.data['modifier_groups'][0]['max_selections'], 2)
        quote = self.client.post(self.path('quote/'), {'items':[
            {'product_id':pid, 'quantity':1, 'modifier_option_ids':['option-a', 'option-b']}]}, format='json')
        self.assertEqual(quote.status_code, 200, quote.data)
        self.assertEqual(quote.data['subtotal'], '135.00')
        for option_ids in ([], ['option-a', 'option-b', 'option-c']):
            invalid_quote = self.client.post(self.path('quote/'), {'items':[
                {'product_id':pid, 'quantity':1, 'modifier_option_ids':option_ids}]}, format='json')
            self.assertEqual(invalid_quote.status_code, 400, invalid_quote.data)
        res = self.client.patch(self.path(f'products/{pid}/'), {'name':'Updated', 'modifier_groups':[
            {'name':'Broken', 'min_selections':3, 'max_selections':1, 'options':[]}]}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(Product.objects.get(pk=pid).name, 'Meal')
        res = self.client.patch(self.path(f'products/{pid}/'), {'name':'Updated'}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['variants'][0]['id'], 'size-a')
        self.assertEqual({option['id'] for option in res.data['modifier_groups'][0]['options']},
            {'option-a', 'option-b', 'option-c'})

    def test_tenant_isolation_and_no_cost_leak(self):
        res = self.client.patch(self.path(f'products/{self.foreign.pk}/'), {'name':'Attack'}, format='json')
        self.assertEqual(res.status_code, 404)
        res = self.client.get(f'/api/v1/catalog/management/?outlet_id={self.other_branch.pk}')
        self.assertEqual(res.status_code, 403)
        self.client.force_authenticate(None)
        res = self.client.get(self.path('menu/'))
        self.assertEqual(res.status_code, 200)
        rows = [p for c in res.data['categories'] for p in c['products']]
        self.assertEqual([p['id'] for p in rows], [self.product.pk])
        self.assertNotIn('cost_price', rows[0])
        self.assertNotIn('recipe_ingredients', rows[0])
        self.assertEqual(self.client.get(self.path('products/')).status_code, 401)

    def test_revision_cache_and_rollback(self):
        first = get_outlet_menu(self.branch.pk, 'web')
        before = MenuOutboxEvent.objects.count()
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                product_update(self.product, base_price=Decimal('777'))
                raise RuntimeError('rollback')
        self.assertEqual(MenuOutboxEvent.objects.count(), before)
        self.assertEqual(get_outlet_menu(self.branch.pk, 'web'), first)
        product_update(self.product, base_price=Decimal('500'))
        updated = get_outlet_menu(self.branch.pk, 'web')
        self.assertGreater(updated['revision'], first['revision'])
        self.assertEqual(updated['categories'][0]['products'][0]['base_price'], '500.00')
        with self.assertNumQueries(2):
            get_outlet_menu(self.branch.pk, 'web')

    def test_schedule_overnight_and_quote_price(self):
        schedule_save(self.branch, {'name':'Monday night', 'start_time':time(22), 'end_time':time(2),
            'days':['Mon'], 'channels':['web'], 'adjustment_percentage':Decimal('20'), 'discount_percentage':Decimal('0'), 'product_ids':[]})
        now = datetime(2026, 9, 29, 1, 0, tzinfo=ZoneInfo('Asia/Kathmandu'))
        row = {'product_id':self.product.pk, 'quantity':2}
        self.assertEqual(quote_items(self.branch, [row], 'web', now)['subtotal'], '960.00')
        self.assertEqual(quote_items(self.branch, [row], 'qr', now)['subtotal'], '800.00')
        self.assertFalse(active_window(time(22), time(2), ['Mon'], now.replace(hour=2)))
        self.assertFalse(active_window(time(22), time(2), ['Tue'], now))

    def test_combo_prices_are_derived_and_custom_choices_validated(self):
        side = product_create(category=self.category, name='Fries', base_price=Decimal('100'))
        payload = {'category':self.category.pk, 'name':'Bundle', 'base_price':'1', 'is_combo_package':True,
            'combo_discount_type':'percentage', 'combo_discount_value':'10', 'combo_items':[
                {'product_id':self.product.pk, 'quantity':1, 'unit_price':'1'}, {'product_id':side.pk, 'quantity':2}]}
        res = self.client.post(self.path('products/'), payload, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data['base_price'], '540.00')
        combo = Product.objects.get(pk=res.data['id'])
        quote = quote_items(self.branch, [{'product_id':combo.pk, 'quantity':1, 'combo_selections':[
            {'product_id':self.product.pk, 'quantity':1, 'modifier_option_ids':[]}]}], 'web')
        self.assertEqual(quote['subtotal'], '390.00')
        self.assertEqual(len(quote['items'][0]['combo_components']), 1)
        # Changing a component updates the derived baseline.
        product_update(side, base_price=Decimal('150'))
        combo.refresh_from_db()
        self.assertEqual(combo.base_price, Decimal('630'))
        bad = self.client.post(self.path('quote/'), {'items':[{'product_id':self.product.pk, 'modifier_option_ids':['forged']}]}, format='json')
        self.assertEqual(bad.status_code, 400)

    def test_override_visibility_master_stock_and_clear_price(self):
        outlet_update_product(self.branch, self.product, is_web_visible=False, price_override=Decimal('500'))
        self.assertEqual(get_outlet_menu(self.branch.pk, 'web')['categories'], [])
        res = self.client.post(self.path(f'outlets/me/products/{self.product.pk}/toggle-stock/'), {'price_override':None}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertIsNone(res.data['override']['price_override'])
        product_update(self.product, is_available=False)
        outlet_update_product(self.branch, self.product, is_available=True, is_web_visible=True)
        self.assertFalse(get_outlet_menu(self.branch.pk, 'web')['categories'][0]['products'][0]['is_available'])

    def test_etag_validation_invalid_outlets_and_cache_failure(self):
        self.client.force_authenticate(None)
        res = self.client.get(self.path('menu/'))
        self.assertEqual(self.client.get(self.path('menu/'), HTTP_IF_NONE_MATCH=res['ETag']).status_code, 304)
        self.assertEqual(self.client.get('/api/v1/catalog/menu/?outlet_id=garbage').status_code, 400)
        self.assertEqual(self.client.get(self.path('menu/')+'&channel=garbage').status_code, 400)
        with patch('apps.catalog.selectors.cache.get', side_effect=ConnectionError('offline')):
            self.assertEqual(get_outlet_menu(self.branch.pk, 'web')['categories'][0]['products'][0]['name'], 'Burger')

    def test_outbox_failure_is_durable_and_retriable(self):
        from .tasks import publish_menu_events
        with patch('apps.catalog.tasks.get_channel_layer', side_effect=ConnectionError('offline')):
            publish_menu_events()
        event = MenuOutboxEvent.objects.filter(branch=self.branch).first()
        event.refresh_from_db()
        self.assertIsNone(event.published_at)
        self.assertEqual(event.attempts, 1)
        self.assertIn('offline', event.last_error)
        MenuOutboxEvent.objects.update(next_attempt_at=timezone.now())
        publish_menu_events()
        event.refresh_from_db()
        self.assertIsNotNone(event.published_at)

    def test_invalid_inputs_and_schedule_crud(self):
        for body in ({'subtotal':'NaN'}, {'subtotal':'Infinity'}, {'subtotal':'-1'}):
            self.assertEqual(self.client.post(self.path('calculate-pricing/'), body, format='json').status_code, 400)
        body = {'name':'Happy hour', 'start_time':'16:00', 'end_time':'19:00', 'days':['Mon'], 'channels':['web'], 'adjustment_percentage':'-15', 'product_ids':[self.product.pk]}
        res = self.client.post(self.path('schedules/'), body, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        sid = res.data['id']
        self.assertEqual(self.client.patch(self.path(f'schedules/{sid}/'), {'is_active':False}, format='json').status_code, 200)
        self.assertEqual(self.client.delete(self.path(f'schedules/{sid}/')).status_code, 204)
        body['product_ids'] = [self.foreign.pk]
        self.assertEqual(self.client.post(self.path('schedules/'), body, format='json').status_code, 400)
