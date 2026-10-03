import io
import json
import tempfile
from decimal import Decimal
from PIL import Image
from django.contrib.auth.hashers import make_password, check_password
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from apps.user_accounts.models import User
from apps.restaurants.models import Restaurant, Branch
from apps.catalog.models import Category, Product, ProductVariant
from apps.orders.models import Order
from apps.orders.tasks import publish_pos_events
from .models import CustomerProfile, CustomerOrder, CustomerContact, SignupChallenge


@override_settings(CUSTOMER_DEMO_OTP=True, CHANNEL_LAYERS={'default': {'BACKEND':'channels.layers.InMemoryChannelLayer'}})
class CustomerFlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner=User.objects.create_user(username='owner',role='RESTAURANT_OWNER',password='Owner-password99')
        cls.brand=Restaurant.objects.create(name='Customer brand',admin=cls.owner,payment_qr='payment_qr/merchant.png')
        cls.owner.restaurant=cls.brand;cls.owner.save()
        cls.branch=Branch.objects.create(name='Customer outlet',restaurant=cls.brand,branch_code='WEB1')
        cls.category=Category.objects.create(name='Food',restaurant=cls.brand)
        cls.product=Product.objects.create(name='Burger',category=cls.category,base_price=200)
        cls.variant=ProductVariant.objects.create(product=cls.product,name='Large',price=250)
        cls.drink=Product.objects.create(name='Drink',category=cls.category,base_price=100,requires_kitchen=False)
        cls.combo=Product.objects.create(name='Combo',category=cls.category,base_price=270,is_combo_package=True,
            combo_discount_type='fixed_price',combo_discount_value=270,combo_items=[{'product_id':str(cls.product.pk),'quantity':1},{'product_id':str(cls.drink.pk),'quantity':1}])
        cls.customer=User.objects.create_user(username='customer',phone_number='+9779800000011',password='Customer-secret99')
        CustomerProfile.objects.create(user=cls.customer,pin_hash=make_password('4567'))
        cls.other=User.objects.create_user(username='other',phone_number='+9779800000022',password='Other-secret99')

    def setUp(self):
        cache.clear();self.client=APIClient()
        self.media=tempfile.TemporaryDirectory();self.addCleanup(self.media.cleanup)
        self.settings_override=override_settings(MEDIA_ROOT=self.media.name);self.settings_override.enable();self.addCleanup(self.settings_override.disable)

    def image(self):
        buffer=io.BytesIO();Image.new('RGB',(8,8),'white').save(buffer,format='PNG')
        return SimpleUploadedFile('receipt.png',buffer.getvalue(),content_type='image/png')

    def post(self,path,data):
        return self.client.post('/api/v1/customer/'+path,data,format='json')

    def payload(self,**extra):
        return {'outlet_id':self.branch.pk,'items':[{'product_id':str(self.product.pk),'quantity':1}],
            'fulfillment_type':'TAKEAWAY','customer_name':'Customer','tip':'0',**extra}

    def checkout(self,data=None,key='one'):
        data=data or self.payload()
        quote=self.post('checkout/quote/',data)
        self.assertEqual(quote.status_code,200,quote.data)
        return self.client.post('/api/v1/customer/checkout/',{'payload':json.dumps({**data,'expected_total':quote.data['total_payable']}),'receipt':self.image()},HTTP_IDEMPOTENCY_KEY=key)

    def test_signup_requires_exact_code_and_details_then_auto_session(self):
        started=self.post('auth/start/',{'phone':'9841234567'})
        self.assertEqual(started.status_code,200,started.data)
        wrong=self.post('auth/verify/',{'challenge_id':started.data['challenge_id'],'code':'xxxx'})
        self.assertEqual(wrong.status_code,400)
        verified=self.post('auth/verify/',{'challenge_id':started.data['challenge_id'],'code':started.data['demo_code']})
        body={'registration_token':verified.data['registration_token'],'username':'new_customer','email':'','pin':'1234','password':'Crisp!River49Ocean'}
        registered=self.post('auth/register/',body)
        self.assertEqual(registered.status_code,201,registered.data)
        user=User.objects.get(username='new_customer')
        self.assertFalse(user.is_staff);self.assertFalse(user.is_verified)
        self.assertTrue(check_password('1234',user.web_profile.pin_hash));self.assertNotEqual(user.web_profile.pin_hash,'1234')
        self.assertEqual(self.post('auth/register/',body).status_code,400)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer '+registered.data['access'])
        self.assertEqual(self.client.get('/api/v1/customer/profile/').status_code,200)

    def test_existing_phone_cannot_use_demo_code_to_log_in(self):
        response=self.post('auth/start/',{'phone':'9800000011'})
        self.assertEqual(response.data,{'exists':True})
        for method,credential in [('PIN','4567'),('PASSWORD','Customer-secret99')]:
            response=self.post('auth/login/',{'phone':'9800000011','method':method,'credential':credential})
            self.assertEqual(response.status_code,200,response.data);self.assertIn('refresh',response.data)
        self.assertEqual(self.post('auth/login/',{'phone':'9800000011','method':'PIN','credential':'0000'}).status_code,403)

    def test_guest_phone_can_register_and_then_log_in(self):
        for stored_phone in ['+9779841234567', '9841234567']:
            with self.subTest(stored_phone=stored_phone):
                guest = User.objects.create_user(username='guest', phone_number=stored_phone)
                self.complete_guest_signup(guest)
                guest.delete()
                SignupChallenge.objects.all().delete()
                cache.clear()

    def complete_guest_signup(self, guest):
        started = self.post('auth/start/', {'phone':'9841234567'})
        self.assertEqual(started.status_code, 200, started.data)
        self.assertFalse(started.data['exists'])
        verified = self.post('auth/verify/', {'challenge_id':started.data['challenge_id'], 'code':started.data['demo_code']})
        body = {'registration_token':verified.data['registration_token'], 'username':'registered_guest',
                'pin':'9274', 'password':'Crisp!River49Ocean'}
        registered = self.post('auth/register/', body)
        self.assertEqual(registered.status_code, 201, registered.data)
        self.assertEqual(User.objects.get(username='registered_guest').pk, guest.pk)
        guest.refresh_from_db()
        self.assertEqual(guest.phone_number, '+9779841234567')
        self.assertEqual(self.post('auth/register/', body).status_code, 400)
        for method, credential in [('PIN','9274'), ('PASSWORD',body['password'])]:
            response = self.post('auth/login/', {'phone':'9841234567', 'method':method, 'credential':credential})
            self.assertEqual(response.status_code, 200, response.data)

    def test_signup_cannot_claim_protected_accounts(self):
        cases = [
            {'password':'Existing-secret99'}, {'role':'CASHIER'},
            {'is_staff':True}, {'is_superuser':True}, {'is_active':False},
        ]
        for fields in cases:
            with self.subTest(fields=fields):
                user = User.objects.create_user(username='protected', phone_number='9841234567', **fields)
                self.assertEqual(self.post('auth/start/', {'phone':'9841234567'}).data, {'exists':True})
                user.delete()

    def test_order_contact_does_not_block_signup(self):
        contact = CustomerContact.objects.create(branch=self.branch, phone='+9779841234567', sources=['POS','QR','KIOSK'])
        started = self.post('auth/start/', {'phone':'9841234567'})
        self.assertEqual(started.status_code, 200, started.data)
        self.assertFalse(started.data['exists'])
        self.assertIn('challenge_id', started.data)
        contact.refresh_from_db()
        self.assertEqual(contact.sources, ['POS','QR','KIOSK'])

    def test_registration_rechecks_guest_credentials_after_verification(self):
        guest = User.objects.create_user(username='guest', phone_number='9841234567')
        started = self.post('auth/start/', {'phone':'9841234567'})
        verified = self.post('auth/verify/', {'challenge_id':started.data['challenge_id'], 'code':started.data['demo_code']})
        CustomerProfile.objects.create(user=guest, pin_hash=make_password('4567'))
        response = self.post('auth/register/', {'registration_token':verified.data['registration_token'],
            'username':'replacement', 'pin':'9274', 'password':'Crisp!River49Ocean'})
        self.assertEqual(response.status_code, 400, response.data)
        guest.refresh_from_db()
        self.assertEqual(guest.username, 'guest')
        self.assertTrue(check_password('4567', guest.web_profile.pin_hash))
        self.assertFalse(SignupChallenge.objects.get(pk=started.data['challenge_id']).consumed)

    def test_pin_locks_after_five_failures_but_password_can_log_in(self):
        for _ in range(5):
            self.assertEqual(self.post('auth/login/',{'phone':'9800000011','method':'PIN','credential':'0000'}).status_code,403)
        self.assertEqual(self.post('auth/login/',{'phone':'9800000011','method':'PIN','credential':'4567'}).status_code,403)
        self.assertEqual(self.post('auth/login/',{'phone':'9800000011','method':'PASSWORD','credential':'Customer-secret99'}).status_code,200)

    def test_authenticated_receipt_checkout_is_pending_and_idempotent(self):
        self.assertEqual(self.post('checkout/quote/',self.payload()).status_code,401)
        self.client.force_authenticate(self.customer)
        one=self.checkout();two=self.checkout()
        self.assertEqual(one.status_code,201,one.data);self.assertEqual(two.status_code,200,two.data)
        self.assertEqual(one.data['id'],two.data['id']);self.assertEqual(Order.objects.count(),1)
        self.assertEqual(one.data['paid_amount'],'0.00');self.assertEqual(one.data['payment_review'],'PENDING')
        self.assertEqual(one.data['order_source'],'WEBSITE');self.assertEqual(one.data['status'],'PENDING')
        self.assertTrue(CustomerOrder.objects.get().receipt_image)
        publish_pos_events()

    def test_combo_choices_and_tip_survive_order_and_billing_quote(self):
        self.client.force_authenticate(self.customer)
        data=self.payload(tip='20',items=[{'product_id':str(self.combo.pk),'quantity':2,'combo_selections':[
            {'product_id':str(self.product.pk),'variant_id':str(self.variant.pk),'quantity':1},
            {'product_id':str(self.drink.pk),'quantity':1}]}])
        created=self.checkout(data)
        self.assertEqual(created.status_code,201,created.data)
        self.assertTrue(created.data['items'][0]['combo_components'])
        self.assertEqual(created.data['reorder_items'][0]['combo_selections'][0]['variant_id'],str(self.variant.pk))
        self.client.force_authenticate(self.owner)
        bill=self.client.post(f'/api/v1/orders/pos/{created.data["id"]}/billing-quote/?outlet_id={self.branch.pk}',{'version':1},format='json')
        self.assertEqual(bill.status_code,200,bill.data);self.assertEqual(Decimal(bill.data['total_payable']),Decimal(created.data['total_payable']))

    def test_no_receipt_or_changed_price_cannot_create_order(self):
        self.client.force_authenticate(self.customer)
        body=self.payload(expected_total='1')
        self.assertEqual(self.client.post('/api/v1/customer/checkout/',{'payload':json.dumps(body)},HTTP_IDEMPOTENCY_KEY='x').status_code,400)
        response=self.client.post('/api/v1/customer/checkout/',{'payload':json.dumps(body),'receipt':self.image()},HTTP_IDEMPOTENCY_KEY='x')
        self.assertEqual(response.status_code,409,response.data);self.assertFalse(Order.objects.exists())

    def test_orders_receipts_and_favorites_are_private(self):
        self.client.force_authenticate(self.customer);order=self.checkout().data
        self.assertEqual(self.post(f'favorites/{self.product.pk}/',{'selected':True}).status_code,200)
        self.assertEqual(self.client.get('/api/v1/customer/orders/').data['results'][0]['id'],order['id'])
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get('/api/v1/customer/orders/').data['results'],[])
        self.assertEqual(self.client.get('/api/v1/customer/profile/').data['favorites'],[])
        self.assertEqual(self.client.get(f'/api/v1/customer/orders/{order["id"]}/receipt/').status_code,403)
        self.assertEqual(self.post(f'orders/{order["id"]}/cancel/',{}).status_code,404)
        self.client.force_authenticate(self.customer)
        self.assertEqual(self.client.get(f'/api/v1/customer/orders/{order["id"]}/receipt/').status_code,200)

    def test_cancel_updates_real_order_and_cannot_cancel_accepted(self):
        self.client.force_authenticate(self.customer);order=self.checkout().data
        self.assertEqual(self.post(f'orders/{order["id"]}/cancel/',{}).data['status'],'CANCELLED')
        second=self.checkout(key='two').data
        Order.objects.filter(pk=second['id']).update(status='ACCEPTED')
        self.assertEqual(self.post(f'orders/{second["id"]}/cancel/',{}).status_code,400)

    def test_organization_qr_upload_is_real_and_customer_cannot_change_it(self):
        self.client.force_authenticate(self.customer)
        self.assertEqual(self.client.patch('/api/v1/organization/',{'payment_qr':self.image()}).status_code,403)
        self.client.force_authenticate(self.owner)
        result=self.client.patch('/api/v1/organization/',{'payment_qr':self.image()})
        self.assertEqual(result.status_code,200,result.data);self.assertIn('/payment_qr/',result.data['payment_qr'])
        meta=self.client.get(f'/api/v1/customer/checkout/meta/?outlet_id={self.branch.pk}')
        self.assertEqual(meta.data['qr_url'],result.data['payment_qr'])

    def cart_item(self, line_id='guest-line'):
        return {'cartItemId': line_id, 'productId': str(self.product.pk), 'productName': 'Burger',
                'image': '', 'variant': {'id': '', 'name': 'Standard', 'price': 200},
                'selectedModifiers': [], 'quantity': 1, 'unitPrice': 200, 'lineTotal': 200,
                'addedAt': 1, 'quoteExpiresAt': 2}

    def test_cart_merge_retries_conflicts_and_account_isolation(self):
        import uuid
        path = f'/api/v1/customer/cart/?outlet_id={self.branch.pk}'
        self.assertEqual(self.client.get(path).status_code, 401)
        self.client.force_authenticate(self.customer)
        saved = self.client.put(path, {'items': [self.cart_item('account-line')], 'version': 0}, format='json')
        self.assertEqual(saved.status_code, 200, saved.data)
        merge = {'items': [self.cart_item()], 'merge_id': str(uuid.uuid4())}
        first = self.client.post(path, merge, format='json')
        self.assertEqual(first.status_code, 200, first.data)
        again = self.client.post(path, merge, format='json')
        self.assertEqual(first.data, again.data)
        self.assertEqual(len(first.data['items']), 2)
        self.assertEqual(self.client.put(path, {'items': [], 'version': 0}, format='json').status_code, 409)
        cleared = self.client.put(path, {'items': [], 'version': first.data['version']}, format='json')
        self.assertEqual(cleared.status_code, 200)
        self.assertEqual(self.client.post(path, merge, format='json').data['items'], [])
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(path).data, {'items': [], 'version': 0})
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.get(path).status_code, 403)

    def test_cart_rejects_other_restaurant_products_and_invalid_quantities(self):
        self.client.force_authenticate(self.customer)
        brand = Restaurant.objects.create(name='Other brand', slug='other-cart-brand', admin=self.owner)
        category = Category.objects.create(name='Other', restaurant=brand)
        product = Product.objects.create(name='Other food', category=category, base_price=100)
        path = f'/api/v1/customer/cart/?outlet_id={self.branch.pk}'
        item = {**self.cart_item(), 'productId': str(product.pk)}
        self.assertEqual(self.client.put(path, {'items': [item], 'version': 0}, format='json').status_code, 400)
        item = {**self.cart_item(), 'quantity': -1}
        self.assertEqual(self.client.put(path, {'items': [item], 'version': 0}, format='json').status_code, 400)

    def test_addresses_are_private_and_have_one_default(self):
        self.client.force_authenticate(self.customer)
        home = self.post('addresses/', {'label': 'Home', 'address': 'My real street', 'latitude': '27.7000000', 'longitude': '85.3000000'})
        self.assertEqual(home.status_code, 201, home.data)
        self.assertTrue(home.data['is_default'])
        repeated = self.post('addresses/', {'label': 'Home', 'address': 'My real street', 'latitude': '27.7000000', 'longitude': '85.3000000'})
        self.assertEqual(repeated.data['id'], home.data['id'])
        work = self.post('addresses/', {'label': 'Work', 'address': 'Office street', 'is_default': True})
        self.assertEqual(work.status_code, 201)
        rows = self.client.get('/api/v1/customer/addresses/').data
        self.assertEqual(sum(row['is_default'] for row in rows), 1)
        self.assertEqual(rows[0]['label'], 'Work')
        self.assertEqual(self.post('addresses/', {'label': 'Bad', 'address': 'Street', 'latitude': 100, 'longitude': 0}).status_code, 400)
        self.client.force_authenticate(self.other)
        path = f'/api/v1/customer/addresses/{home.data["id"]}/'
        self.assertEqual(self.client.patch(path, {'address': 'stolen'}, format='json').status_code, 404)
        self.assertEqual(self.client.delete(path).status_code, 404)
        self.assertEqual(self.client.get('/api/v1/customer/addresses/').data, [])
        self.client.force_authenticate(self.customer)
        self.client.delete(f'/api/v1/customer/addresses/{work.data["id"]}/')
        self.assertTrue(self.client.get('/api/v1/customer/addresses/').data[0]['is_default'])

    def test_checkout_keeps_exact_coordinates_and_consumes_cart_once(self):
        self.client.force_authenticate(self.customer)
        path = f'/api/v1/customer/cart/?outlet_id={self.branch.pk}'
        self.client.put(path, {'items': [self.cart_item()], 'version': 0}, format='json')
        point = {'lat': 27.701234, 'lng': 85.309876, 'landmark': 'Blue gate'}
        data = self.payload(fulfillment_type='DELIVERY', delivery_address='Real street', delivery_location=point, cart_line_ids=['guest-line'])
        first = self.checkout(data)
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(first.data['delivery_location'], point)
        self.assertIn('27.701234,85.309876', first.data['delivery_address'])
        self.assertEqual(self.client.get(path).data['items'], [])
        version = self.client.get(path).data['version']
        self.client.put(path, {'items': [self.cart_item()], 'version': version}, format='json')
        self.assertEqual(self.checkout(data).data['id'], first.data['id'])
        self.assertEqual(len(self.client.get(path).data['items']), 1)

