from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework import status
from apps.user_accounts.models import User, UserRole
from apps.user_accounts.services import superuser_create, user_create
from apps.restaurants.models import Restaurant, Branch
from apps.restaurants.services import restaurant_create, restaurant_admin_create, branch_create
from apps.restaurants.selectors import (
    get_restaurant_by_id,
    get_restaurant_by_slug,
    list_branches_by_restaurant,
    get_branch_by_code,
)


class RestaurantDomainTests(TestCase):
    def setUp(self):
        self.client = APIClient()

        # Create Superuser
        self.superuser = superuser_create(
            username="admin_super",
            email="super@crunchy.local",
            phone_number="+15551112222",
            password="SuperPassword123!",
        )

        # Create Restaurant Admin
        self.restaurant_admin = user_create(
            username="owner_ram",
            email="ram@crunchy.local",
            phone_number="+15553334444",
            password="OwnerPassword123!",
            role=UserRole.RESTAURANT_OWNER,
            is_staff=True,
        )

        # Create Customer
        self.customer = user_create(
            username="john_customer",
            email="john@customer.local",
            phone_number="+15555556666",
            password="CustomerPass123!",
            role=UserRole.CUSTOMER,
        )

    def test_restaurant_and_branch_service_creation(self):
        # 1. Create brand
        brand = restaurant_create(
            name="Crunchy Bag Fried Chicken",
            admin_user=self.restaurant_admin,
            pan_number="123456789",
            phone="+977-1-4433221",
        )
        self.assertEqual(brand.slug, "crunchy-bag-fried-chicken")
        self.assertEqual(self.restaurant_admin.restaurant_id, brand.id)
        self.assertEqual(self.restaurant_admin.role, UserRole.RESTAURANT_OWNER)

        # 2. Create Branches (Multi-Outlet Franchise)
        branch1 = branch_create(
            restaurant=brand,
            name="Kathmandu Flagship",
            branch_code="CB-KTM-01",
            city="Kathmandu",
            is_main_branch=True,
        )
        branch2 = branch_create(
            restaurant=brand,
            name="Lalitpur Hub",
            branch_code="CB-LLP-01",
            city="Lalitpur",
            is_main_branch=False,
        )

        self.assertEqual(brand.branches.count(), 2)
        self.assertTrue(branch1.is_main_branch)
        self.assertFalse(branch2.is_main_branch)

    def test_atomic_restaurant_and_admin_creation(self):
        brand, new_admin = restaurant_admin_create(
            restaurant_name="Crunchy Bag Nepal",
            admin_username="nepal_owner",
            admin_email="nepal_owner@crunchy.local",
            admin_phone="+9779800000000",
            admin_password="SecurePassword123!",
            pan_number="987654321",
        )
        self.assertEqual(brand.admin_id, new_admin.id)
        self.assertEqual(new_admin.role, UserRole.RESTAURANT_OWNER)
        self.assertTrue(new_admin.is_staff)
        self.assertEqual(new_admin.restaurant_id, brand.id)

    def test_selectors(self):
        brand = restaurant_create(
            name="Crunchy Burger House",
            admin_user=self.restaurant_admin,
        )
        branch = branch_create(
            restaurant=brand,
            name="Downtown Express",
            branch_code="CBH-DT-01",
        )

        by_slug = get_restaurant_by_slug("crunchy-burger-house")
        self.assertIsNotNone(by_slug)
        self.assertEqual(by_slug.id, brand.id)

        by_code = get_branch_by_code("CBH-DT-01")
        self.assertIsNotNone(by_code)
        self.assertEqual(by_code.id, branch.id)

        branches = list_branches_by_restaurant(brand.id)
        self.assertEqual(branches.count(), 1)

    def test_api_restaurant_creation_permissions(self):
        # Customer should be forbidden from creating a restaurant
        self.client.force_authenticate(user=self.customer)
        res = self.client.post('/api/v1/restaurants/', {
            'name': 'Unauthorized Brand',
        })
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

        # Superuser can create a restaurant brand
        self.client.force_authenticate(user=self.superuser)
        res = self.client.post('/api/v1/restaurants/', {
            'name': 'Crunchy Bag Global',
            'admin': self.restaurant_admin.id,
            'pan_number': '600123456',
            'phone': '+977-1-5554433',
        })
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data['name'], 'Crunchy Bag Global')
        self.assertEqual(res.data['slug'], 'crunchy-bag-global')

        created_id = res.data['id']

        # Brand Admin can add branches to their brand
        self.client.force_authenticate(user=self.restaurant_admin)
        branch_res = self.client.post(f'/api/v1/restaurants/{created_id}/branches/', {
            'name': 'Airport Hub',
            'branch_code': 'CBG-AIR-01',
            'city': 'Kathmandu',
            'is_main_branch': True,
        })
        self.assertEqual(branch_res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(branch_res.data['branch_code'], 'CBG-AIR-01')

