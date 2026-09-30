from datetime import timedelta
from unittest.mock import patch, MagicMock
from urllib.parse import parse_qs
from django.test import TestCase, override_settings
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APIClient
from apps.user_accounts.models import User
from apps.restaurants.models import Restaurant, Branch
from .models import SignupChallenge, SmsDelivery
from .secrets import seal, unseal
from .tasks import send_pending_sms


@override_settings(CUSTOMER_DEMO_OTP=False)
class SmsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.owner = User.objects.create_user(username='owner', role='RESTAURANT_OWNER')
        self.org = Restaurant.objects.create(name='Kitchen', admin=self.owner, sms_enabled=True,
            sms_sender='Approved', sms_token_encrypted=seal('private-provider-token'))
        self.owner.restaurant = self.org
        self.owner.save()
        self.branch = Branch.objects.create(restaurant=self.org, name='Outlet', branch_code='SMS1')

    def post(self, action, data):
        return self.client.post('/api/v1/customer/auth/'+action+'/', data, format='json')

    def start(self, purpose='start'):
        return self.post(purpose, {'phone':'9841234567','outlet_id':self.branch.pk})

    def test_live_signup_never_returns_code_and_sends_only_to_customer(self):
        result = self.start()
        self.assertEqual(result.status_code, 200, result.data)
        self.assertNotIn('demo_code', result.data)
        row = SmsDelivery.objects.get()
        code = unseal(row.payload_encrypted)
        response = MagicMock(status=200)
        response.read.return_value = b'{"response_code":200,"count":1}'
        with patch('apps.customer_web.tasks.urlopen') as network:
            network.return_value.__enter__.return_value = response
            send_pending_sms()
            request = network.call_args.args[0]
            payload = parse_qs(request.data.decode())
            self.assertEqual(request.full_url, 'https://api.sparrowsms.com/v2/sms/')
            self.assertEqual(payload['to'], ['9841234567'])
            self.assertEqual(payload['from'], ['Approved'])
            self.assertIn(code, payload['text'][0])
            send_pending_sms()
            self.assertEqual(network.call_count, 1)
        row.refresh_from_db()
        self.assertEqual(row.status, 'SENT')
        self.assertEqual(row.payload_encrypted, '')
        verified = self.post('verify', {'challenge_id':result.data['challenge_id'],'code':code})
        self.assertEqual(verified.status_code, 200)
        self.assertEqual(self.post('verify', {'challenge_id':result.data['challenge_id'],'code':code}).status_code, 400)

    def test_recovery_is_purpose_bound_and_single_use(self):
        user = User.objects.create_user(username='customer', phone_number='+9779841234567', role='CUSTOMER', password='Old-secret88')
        result = self.start('recovery-start')
        code = unseal(SmsDelivery.objects.get().payload_encrypted)
        data = {'challenge_id':result.data['challenge_id'],'code':code}
        self.assertEqual(self.post('verify', data).status_code, 400)
        verified = self.post('recovery-verify', data)
        self.assertEqual(verified.status_code, 200, verified.data)
        body = {'reset_token':verified.data['reset_token'],'password':'New-Passphrase-8*Forest','pin':'9274'}
        self.assertEqual(self.post('reset', body).status_code, 200)
        user.refresh_from_db()
        self.assertTrue(user.check_password(body['password']))
        self.assertEqual(self.post('reset', body).status_code, 400)
        self.assertEqual(self.post('login', {'phone':'9841234567','method':'PIN','credential':'9274'}).status_code, 200)

    def test_invalid_number_cooldown_attempts_and_expiry(self):
        self.assertEqual(self.post('start', {'phone':'123'}).status_code, 400)
        result = self.start()
        self.assertEqual(self.start().status_code, 429)
        for unused in range(5):
            self.assertEqual(self.post('verify', {'challenge_id':result.data['challenge_id'],'code':'wrong'}).status_code, 400)
        row = SmsDelivery.objects.get()
        code = unseal(row.payload_encrypted)
        cache.clear()
        self.assertEqual(self.post('verify', {'challenge_id':result.data['challenge_id'],'code':code}).status_code, 400)
        SignupChallenge.objects.update(expires_at=timezone.now()-timedelta(seconds=1))
        with patch('apps.customer_web.tasks.urlopen') as network:
            send_pending_sms()
            network.assert_not_called()
        row.refresh_from_db()
        self.assertEqual(row.payload_encrypted, '')

    def test_provider_errors_retry_without_exposing_credentials(self):
        self.start()
        with patch('apps.customer_web.tasks.urlopen', side_effect=TimeoutError('secret detail')):
            send_pending_sms()
        row = SmsDelivery.objects.get()
        self.assertEqual(row.status, 'PENDING')
        self.assertEqual(row.last_error, 'transport_or_configuration_error')
        SmsDelivery.objects.update(attempts=3, next_attempt_at=timezone.now())
        with patch('apps.customer_web.tasks.urlopen', side_effect=TimeoutError()):
            send_pending_sms()
        row.refresh_from_db()
        self.assertEqual(row.status, 'FAILED')
        self.assertEqual(row.payload_encrypted, '')

    def test_settings_secret_is_write_only_encrypted_and_tenant_scoped(self):
        self.client.force_authenticate(self.owner)
        path = '/api/v1/organization/'
        result = self.client.patch(path, {'sms_api_token':'replacement','sms_admin_numbers':'9841234567,9800000000'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertNotIn('replacement', str(result.data))
        self.assertNotIn('sms_token_encrypted', result.data)
        self.org.refresh_from_db()
        self.assertNotEqual(self.org.sms_token_encrypted, 'replacement')
        self.assertEqual(unseal(self.org.sms_token_encrypted), 'replacement')
        self.client.patch(path, {'sms_api_token':''}, format='json')
        self.org.refresh_from_db()
        self.assertEqual(unseal(self.org.sms_token_encrypted), 'replacement')
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(path).status_code, [401,403])
        self.assertNotIn('sms_', str(self.client.get(f'/api/v1/restaurants/{self.org.pk}/').data))
