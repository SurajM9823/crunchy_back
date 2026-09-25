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


from decimal import Decimal
from apps.restaurants.models import Restaurant, Branch, OperateType
from apps.restaurants.services import restaurant_create, branch_with_admin_create
from apps.user_accounts.models import Employee, SystemRole


class StaffAccessManagementTests(TestCase):
    def setUp(self):
        self.api_client = APIClient()

        # 1. Setup Brand and Branches
        self.owner = user_create(
            username="brand_director",
            email="director@crunchy.com",
            phone_number="+9779800000081",
            password="DirectorPassword123!",
            role=UserRole.RESTAURANT_OWNER,
            is_staff=True,
        )
        self.brand = restaurant_create(
            name="Crunchy Bag Staff Brand",
            admin_user=self.owner,
        )
        self.branch1, self.manager1 = branch_with_admin_create(
            restaurant=self.brand,
            name="Durbar Marg HQ",
            branch_code="CRU-01",
            operate_type=OperateType.DINE_IN,
            admin_username="durbarmarg_mgr",
            admin_phone="+9779800000082",
            admin_password="ManagerPass123!",
        )
        self.branch2, self.manager2 = branch_with_admin_create(
            restaurant=self.brand,
            name="Thamel Branch",
            branch_code="CRU-02",
            operate_type=OperateType.DINE_IN,
            admin_username="thamel_mgr",
            admin_phone="+9779800000083",
            admin_password="ManagerPass123!",
        )

    def test_branch_manager_creates_employee_with_auto_assigned_branch(self):
        """
        When logged-in branch manager creates an employee, the backend automatically
        binds the employee to the manager's branch without requiring outlet selection.
        """
        self.api_client.force_authenticate(user=self.manager1)

        payload = {
            "name": "Bikash Shrestha",
            "email": "bikash.cashier@crunchy.com",
            "phone": "+977 9841234567",
            "role": "CASHIER",
            "title": "Head Cashier & Billing Operator",
            "salary_monthly": "36000.00",
            "pin_code": "4821",
            "temporary_password": "TempPass123!",
        }
        res = self.api_client.post('/api/v1/employees/', data=payload, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data['name'], "Bikash Shrestha")
        self.assertEqual(res.data['assigned_outlet']['name'], "Durbar Marg HQ")
        self.assertEqual(res.data['assigned_outlet']['code'], "CRU-01")
        # Preset auto-populated
        self.assertEqual(res.data['assigned_pages'], ["pos", "daybook", "loyalty"])

        emp = Employee.objects.get(id=res.data['id'])
        self.assertTrue(emp.check_pin("4821"))
        self.assertFalse(emp.check_pin("0000"))

    def test_employee_list_strictly_scoped_to_logged_in_branch(self):
        """
        Manager 1 only sees staff of Durbar Marg HQ, maintaining strict tenant isolation.
        """
        # Create 1 staff in branch 1
        self.api_client.force_authenticate(user=self.manager1)
        self.api_client.post('/api/v1/employees/', data={
            "name": "Staff Branch 1",
            "email": "staff1@crunchy.com",
            "phone": "+9779840000001",
            "role": "CASHIER",
            "salary_monthly": "30000.00",
            "pin_code": "1111",
        }, format='json')

        # Create 1 staff in branch 2
        self.api_client.force_authenticate(user=self.manager2)
        self.api_client.post('/api/v1/employees/', data={
            "name": "Staff Branch 2",
            "email": "staff2@crunchy.com",
            "phone": "+9779840000002",
            "role": "CASHIER",
            "salary_monthly": "32000.00",
            "pin_code": "2222",
        }, format='json')

        # Manager 1 queries list
        self.api_client.force_authenticate(user=self.manager1)
        res1 = self.api_client.get('/api/v1/employees/')
        self.assertEqual(res1.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res1.data['results']), 1)
        self.assertEqual(res1.data['results'][0]['name'], "Staff Branch 1")
        self.assertEqual(res1.data['metrics']['total_staff'], 1)
        self.assertEqual(res1.data['metrics']['total_monthly_payroll'], 30000.0)

        # Manager 2 queries list
        self.api_client.force_authenticate(user=self.manager2)
        res2 = self.api_client.get('/api/v1/employees/')
        self.assertEqual(res2.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res2.data['results']), 1)
        self.assertEqual(res2.data['results'][0]['name'], "Staff Branch 2")
        self.assertEqual(res2.data['metrics']['total_staff'], 1)
        self.assertEqual(res2.data['metrics']['total_monthly_payroll'], 32000.0)

    def test_quick_pos_pin_login_endpoint(self):
        """
        Fast cashier shift switch using 4-digit PIN code.
        """
        self.api_client.force_authenticate(user=self.manager1)
        res_create = self.api_client.post('/api/v1/employees/', data={
            "name": "Sita Sharma",
            "email": "sita@crunchy.com",
            "phone": "+9779849123456",
            "role": "CASHIER",
            "title": "Counter Cashier",
            "pin_code": "4821",
        }, format='json')
        self.assertEqual(res_create.status_code, status.HTTP_201_CREATED)

        # Switch to unauthenticated client (POS terminal)
        anon_client = APIClient()
        pin_res = anon_client.post('/api/v1/auth/staff-pin-login/', data={
            "outlet_id": self.branch1.id,
            "pin_code": "4821",
        }, format='json')
        self.assertEqual(pin_res.status_code, status.HTTP_200_OK)
        self.assertIn('access', pin_res.data)
        self.assertIn('refresh', pin_res.data)
        self.assertEqual(pin_res.data['employee']['name'], "Sita Sharma")
        self.assertEqual(pin_res.data['employee']['role'], "CASHIER")
        self.assertEqual(pin_res.data['outlet']['name'], "Durbar Marg HQ")

        # Wrong PIN test
        wrong_pin = anon_client.post('/api/v1/auth/staff-pin-login/', data={
            "outlet_id": self.branch1.id,
            "pin_code": "9999",
        }, format='json')
        self.assertEqual(wrong_pin.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_super_admin_cannot_be_deleted(self):
        """
        Business Rule: Root SUPER_ADMIN account cannot be deleted (403 Forbidden).
        """
        self.api_client.force_authenticate(user=self.manager1)
        # Create super admin employee
        res_create = self.api_client.post('/api/v1/employees/', data={
            "name": "Rahul Adhikari",
            "email": "rahul.admin@crunchy.com",
            "phone": "+9779851023456",
            "role": "SUPER_ADMIN",
            "title": "General Manager",
            "pin_code": "9999",
        }, format='json')
        emp_id = res_create.data['id']

        del_res = self.api_client.delete(f'/api/v1/employees/{emp_id}/')
        self.assertEqual(del_res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("Cannot delete root SUPER_ADMIN account", del_res.data['detail'])

