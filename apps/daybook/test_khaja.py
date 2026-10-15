from django.test import TestCase
from rest_framework.test import APIClient
from apps.orders import test_pos
from apps.catalog.models import Category, Product
from apps.restaurants.models import Restaurant
from apps.user_accounts.models import User
from .models import KhajaScheduleDay


class KhajaScheduleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        test_pos.PosWorkflowTests.setUpTestData.__func__(cls)

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.manager)
        self.url = f'/api/v1/daybook/khaja-schedule/?outlet_id={self.branch.pk}'

    def save(self, day, products, revision=0):
        return self.client.post(self.url, {'weekday': day, 'product_ids': products, 'revision': revision}, format='json')

    def test_weekly_routine_edit_clear_and_retry(self):
        empty = self.client.get(self.url)
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(len(empty.data['days']), 7)
        self.assertEqual(empty.data['days'][0]['name'], 'Sunday')
        self.assertEqual(empty.data['days'][6]['name'], 'Saturday')
        for day in range(7):
            response = self.save(day, [self.product.pk, self.soda.pk, self.soda.pk])
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(len(response.data['days'][day]['products']), 2)
        retry = self.save(0, [self.product.pk, self.soda.pk])
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.data['days'][0]['revision'], 1)
        edited = self.save(0, [self.soda.pk], 1)
        self.assertEqual(edited.data['days'][0]['revision'], 2)
        self.assertEqual(len(edited.data['days'][1]['products']), 2)
        self.assertEqual(self.save(0, [self.product.pk], 1).status_code, 409)
        cleared = self.save(0, [], 2)
        self.assertEqual(cleared.data['days'][0]['products'], [])
        self.assertEqual(KhajaScheduleDay.objects.count(), 7)
        self.assertEqual(self.client.get(self.url).data['days'][0]['products'], [])
        self.client.force_authenticate(self.owner)
        other = self.client.get(f'/api/v1/daybook/khaja-schedule/?outlet_id={self.other.pk}')
        self.assertTrue(all(not day['products'] for day in other.data['days']))

    def test_validation_permissions_and_archived_products(self):
        brand = Restaurant.objects.create(name='Other brand', admin=self.owner)
        category = Category.objects.create(name='Other menu', restaurant=brand)
        foreign = Product.objects.create(name='Other product', category=category, base_price=20)
        for ids in [[foreign.pk], ['missing']]:
            self.assertEqual(self.save(0, ids).status_code, 400)
        for day in [-1, 7]:
            self.assertEqual(self.save(day, []).status_code, 400)
        self.assertEqual(self.save(0, [self.product.pk]).status_code, 200)
        self.product.is_archived = True
        self.product.save()
        data = self.client.get(self.url).data
        self.assertNotIn(self.product.pk, [p['id'] for p in data['products']])
        self.assertTrue(data['days'][0]['products'][0]['archived'])
        self.assertEqual(self.save(1, [self.product.pk]).status_code, 400)
        self.assertEqual(self.save(0, [self.product.pk, self.soda.pk], 1).status_code, 200)
        outsider = User.objects.create(username='khaja-outsider', role='BRANCH_MANAGER', branch=self.other)
        self.client.force_authenticate(outsider)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.save(0, []).status_code, 403)
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(self.url).status_code, [401, 403])
