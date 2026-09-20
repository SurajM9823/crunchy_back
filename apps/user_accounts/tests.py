from django.test import TestCase, Client
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework import status
from apps.user_accounts.models import User, UserRole
from apps.user_accounts.services import user_create, superuser_create, authenticate_user
from apps.user_accounts.selectors import get_user_by_identifier, get_user_by_phone, get_user_by_email


class UserAuthenticationTests(TestCase):
    def setUp(self):
        self.password = "SecurePass123!"
        self.client = Client()
        self.api_client = APIClient()

        # Create test superuser
        self.superuser = superuser_create(
            username="admin_suraj",
            email="admin@crunchy.local",
            phone_number="+15550192831",
            password=self.password,
        )

        # Create regular staff user
        self.staff_user = user_create(
            username="chef_mario",
            email="mario@crunchy.local",
            phone_number="+15550199999",
            password=self.password,
            role=UserRole.CHEF,
            is_staff=True,
        )

        # Create customer user (non-staff)
        self.customer = user_create(
            username="john_customer",
            email="customer@example.com",
            phone_number="+15550188888",
            password=self.password,
            role=UserRole.CUSTOMER,
        )

    def test_selectors_find_user_by_all_identifiers(self):
        # By Username
        user_by_uname = get_user_by_identifier("admin_suraj")
        self.assertIsNotNone(user_by_uname)
        self.assertEqual(user_by_uname.id, self.superuser.id)

        # By Email
        user_by_email = get_user_by_identifier("admin@crunchy.local")
        self.assertIsNotNone(user_by_email)
        self.assertEqual(user_by_email.id, self.superuser.id)

        # By Phone Number
        user_by_phone = get_user_by_identifier("+15550192831")
        self.assertIsNotNone(user_by_phone)
        self.assertEqual(user_by_phone.id, self.superuser.id)

        # By Formatted Phone Number (spaces/dashes)
        user_by_fmt_phone = get_user_by_identifier("+1 (555) 019-2831")
        self.assertIsNotNone(user_by_fmt_phone)
        self.assertEqual(user_by_fmt_phone.id, self.superuser.id)

    def test_authentication_with_all_identifiers(self):
        # 1. Login with Username
        user1 = authenticate_user(identifier="admin_suraj", password=self.password)
        self.assertIsNotNone(user1)
        self.assertEqual(user1.id, self.superuser.id)

        # 2. Login with Email
        user2 = authenticate_user(identifier="admin@crunchy.local", password=self.password)
        self.assertIsNotNone(user2)
        self.assertEqual(user2.id, self.superuser.id)

        # 3. Login with Phone Number
        user3 = authenticate_user(identifier="+15550192831", password=self.password)
        self.assertIsNotNone(user3)
        self.assertEqual(user3.id, self.superuser.id)

        # 4. Invalid password
        bad_auth = authenticate_user(identifier="admin_suraj", password="wrongpassword")
        self.assertIsNone(bad_auth)

    def test_superuser_web_login_view(self):
        # Test GET loads template
        response = self.client.get(reverse('superuser-login'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Superuser Portal")

        # Test POST with email
        response = self.client.post(reverse('superuser-login'), {
            'identifier': 'admin@crunchy.local',
            'password': self.password,
        })
        self.assertRedirects(response, '/admin/')

        # Logout
        self.client.get(reverse('superuser-logout'))

        # Test POST with phone number
        response = self.client.post(reverse('superuser-login'), {
            'identifier': '+1 (555) 019-2831',
            'password': self.password,
        })
        self.assertRedirects(response, '/admin/')

        # Non-staff customer cannot log into superuser portal
        self.client.get(reverse('superuser-logout'))
        response = self.client.post(reverse('superuser-login'), {
            'identifier': 'customer@example.com',
            'password': self.password,
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Access denied")

    def test_api_login_and_profile(self):
        # API Login with phone number
        response = self.api_client.post(reverse('api-login'), {
            'identifier': '+15550192831',
            'password': self.password,
        })
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('access', response.data)
        self.assertIn('refresh', response.data)
        self.assertEqual(response.data['user']['role'], UserRole.SUPERADMIN)

        token = response.data['access']

        # API Profile access with Bearer token
        self.api_client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        profile_resp = self.api_client.get(reverse('api-profile'))
        self.assertEqual(profile_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(profile_resp.data['username'], "admin_suraj")
        self.assertEqual(profile_resp.data['email'], "admin@crunchy.local")

    def test_health_check_endpoint(self):
        response = self.api_client.get(reverse('api-health'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'online')

