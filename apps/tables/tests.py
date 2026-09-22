from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status

from apps.user_accounts.models import UserRole
from apps.user_accounts.services import user_create
from apps.restaurants.models import OperateType
from apps.restaurants.services import restaurant_create, branch_with_admin_create

from .models import DiningTable
from .services import (
    table_create,
    table_regenerate_qr_salt,
    table_open_dining_session,
    table_close_dining_session,
)
from .qr_security import generate_table_qr_token, verify_and_resolve_qr_token


class DiningTableAndQRTests(TestCase):
    def setUp(self):
        self.client = APIClient()

        # Create brand & branch
        self.owner = user_create(
            username="table_test_owner",
            email="owner@test.local",
            phone_number="+9779800000031",
            password="OwnerPass123!",
            role=UserRole.RESTAURANT_OWNER,
            is_staff=True,
        )
        self.brand = restaurant_create(
            name="Crunchy Bag Table Co",
            admin_user=self.owner,
        )
        self.branch, self.manager = branch_with_admin_create(
            restaurant=self.brand,
            name="Dine-in Hub",
            branch_code="CB-DI-01",
            operate_type=OperateType.DINE_IN,
            admin_username="dinein_mgr",
            admin_phone="+9779800000032",
            admin_password="ManagerPass123!",
        )

        # Create Dining Table
        self.table = table_create(
            branch=self.branch,
            table_number="T-01",
            capacity=4,
            section="Main Dining Hall",
        )

    def test_table_creation_and_qr_token_generation(self):
        self.assertEqual(self.table.table_number, "T-01")
        self.assertTrue(bool(self.table.qr_token_salt))

        # Generate cryptographic QR token
        token = generate_table_qr_token(self.table)
        self.assertIn('.', token)

        # Resolve valid token
        resolved = verify_and_resolve_qr_token(token)
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.id, self.table.id)
        self.assertEqual(resolved.branch_id, self.branch.id)

    def test_tampered_qr_token_rejection(self):
        token = generate_table_qr_token(self.table)
        parts = token.split('.')

        # Tampered payload
        tampered_token = "invalidpayload." + parts[1]
        self.assertIsNone(verify_and_resolve_qr_token(tampered_token))

        # Tampered signature
        bad_sig_token = parts[0] + ".invalidsignature"
        self.assertIsNone(verify_and_resolve_qr_token(bad_sig_token))

    def test_regenerate_qr_salt_invalidates_previous_token(self):
        old_token = generate_table_qr_token(self.table)
        self.assertIsNotNone(verify_and_resolve_qr_token(old_token))

        # Regenerate salt
        table_regenerate_qr_salt(self.table)
        self.table.refresh_from_db()

        # Old token MUST be rejected
        self.assertIsNone(verify_and_resolve_qr_token(old_token))

        # New token MUST work
        new_token = generate_table_qr_token(self.table)
        self.assertIsNotNone(verify_and_resolve_qr_token(new_token))

    def test_table_qr_resolver_api(self):
        token = generate_table_qr_token(self.table)

        # Public resolution via API
        res = self.client.get(f'/api/v1/tables/qr/resolve/?token={token}')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['table_id'], self.table.id)
        self.assertEqual(res.data['table_number'], "T-01")
        self.assertEqual(res.data['branch_code'], "CB-DI-01")
        self.assertEqual(res.data['restaurant_name'], "Crunchy Bag Table Co")

    def test_dining_session_lifecycle(self):
        self.assertIsNone(self.table.active_session_id)

        # Open session
        session_id = table_open_dining_session(self.table)
        self.assertIsNotNone(session_id)
        self.table.refresh_from_db()
        self.assertEqual(self.table.active_session_id, session_id)

        # Close session
        table_close_dining_session(self.table)
        self.table.refresh_from_db()
        self.assertIsNone(self.table.active_session_id)

