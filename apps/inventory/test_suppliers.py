from datetime import timedelta
from decimal import Decimal
from django.test import TestCase
from rest_framework.test import APIClient
from apps.restaurants.models import Restaurant, Branch
from apps.user_accounts.models import User
from apps.daybook.models import DaybookEntry, DaybookEvent
from apps.daybook.serializers import today
from apps.daybook.services import mutate as daybook_mutate
from rest_framework.exceptions import ValidationError
from .models import Supplier, SupplierPayment, PurchaseInvoice, InventoryItem
from .services import supplier_create, purchase_invoice_create, inventory_item_create


class SupplierAccountTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='vendor-owner', role='RESTAURANT_OWNER')
        self.restaurant = Restaurant.objects.create(name='Vendor brand', admin=self.owner)
        self.owner.restaurant = self.restaurant
        self.owner.save()
        self.branch = Branch.objects.create(restaurant=self.restaurant, name='Vendor outlet', branch_code='VEN1')
        other = Restaurant.objects.create(name='Other brand', admin=self.owner)
        self.other = Branch.objects.create(restaurant=other, name='Other outlet', branch_code='VEN2')
        self.supplier = supplier_create(self.branch, 'Fresh Foods', phone='9800000011')
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.base = f'/api/v1/inventory/supplier-accounts/{self.supplier.pk}/'

    def bill(self, total=100, paid=0, **kwargs):
        return purchase_invoice_create(branch=self.branch, invoice_number=f'BILL-{PurchaseInvoice.objects.count()}',
            supplier_name=self.supplier.name, supplier_id=self.supplier.pk, received_by=self.owner,
            items_data=[{'item_name': 'Rice', 'quantity': 2, 'unit_cost': Decimal(total)/2}],
            paid_amount=Decimal(paid), **kwargs)

    def payment(self, amount, key='payment-key', **extra):
        self.supplier.refresh_from_db()
        return self.client.post(f'{self.base}payments/?outlet_id={self.branch.pk}',
            {'amount': str(amount), 'expected_balance': str(self.supplier.credit_balance),
             'date': str(today()), 'method': 'CASH', **extra}, format='json', HTTP_IDEMPOTENCY_KEY=key)

    def account(self):
        response = self.client.get(f'{self.base}?outlet_id={self.branch.pk}')
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_partial_payment_statement_price_and_daybook(self):
        invoice = self.bill(200, 50)
        response = self.payment(75)
        self.assertEqual(response.status_code, 200, response.data)
        data = self.account()
        self.assertEqual(data['summary']['amount_due'], Decimal('75'))
        self.assertEqual(data['summary']['paid_total'], Decimal('125'))
        self.assertEqual(data['invoices'][0]['items'][0]['unit_cost'], '100.00')
        self.assertEqual(data['invoices'][0]['due_amount'], '75.00')
        self.assertEqual(data['statement'][0]['balance'], Decimal('75'))
        entry = DaybookEntry.objects.get(source='SUPPLIER')
        self.assertEqual((entry.amount, entry.direction, entry.party), (Decimal('75'), 'OUT', self.supplier.name))
        self.assertTrue(DaybookEvent.objects.filter(event_type='SUPPLIER_ACCOUNT_UPDATED').exists())

    def test_overpayment_applies_to_future_bill_and_can_be_reversed(self):
        first = self.bill(100)
        paid = self.payment(250)
        self.assertEqual(paid.status_code, 200, paid.data)
        self.assertEqual(self.account()['summary']['advance'], Decimal('150'))
        second = self.bill(200)
        self.assertEqual(second.due_amount, Decimal('50'))
        self.assertEqual(second.paid_amount, Decimal('150'))
        response = self.client.post(f'{self.base}payments/{paid.data["payment_id"]}/void/?outlet_id={self.branch.pk}',
            {'reason': 'Wrong amount recorded'}, format='json', HTTP_IDEMPOTENCY_KEY='void-key')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.account()['summary']['amount_due'], Decimal('300'))
        self.assertIsNotNone(SupplierPayment.objects.get().voided_at)
        self.assertIsNotNone(DaybookEntry.objects.get(source='SUPPLIER').voided_at)
        first.refresh_from_db()
        self.assertEqual(first.due_amount, Decimal('100'))

    def test_retry_does_not_duplicate_payment_and_changed_payload_is_rejected(self):
        self.bill(100)
        first = self.payment(40)
        replay = self.payment(40, expected_balance='100.00')
        self.assertEqual(replay.status_code, 200, replay.data)
        self.assertEqual(first.data, replay.data)
        changed = self.payment(41, expected_balance='100.00')
        self.assertEqual(changed.status_code, 409)
        self.assertEqual(SupplierPayment.objects.count(), 1)
        self.assertEqual(DaybookEntry.objects.filter(source='SUPPLIER').count(), 1)
        stale = self.payment(20, key='new-key', expected_balance='100.00')
        self.assertEqual(stale.status_code, 409)

    def test_opening_balance_and_initial_overpayment(self):
        self.supplier.opening_balance = Decimal('50')
        self.supplier.credit_balance = Decimal('50')
        self.supplier.save()
        invoice = self.bill(100)
        self.payment(75)
        invoice.refresh_from_db()
        self.assertEqual(invoice.due_amount, Decimal('75'))
        self.assertEqual(self.account()['summary']['balance'], Decimal('75'))
        self.bill(20, 120)
        invoice.refresh_from_db()
        self.assertEqual(invoice.due_amount, Decimal('0'))
        self.assertEqual(self.account()['summary']['advance'], Decimal('25'))

    def test_scope_validation_and_daybook_cannot_bypass_reversal(self):
        self.bill()
        self.assertEqual(self.client.get(f'{self.base}?outlet_id={self.other.pk}').status_code, 403)
        for value in ['0', '-1', 'NaN', '1.001']:
            self.assertEqual(self.payment(value).status_code, 400)
        self.assertEqual(self.payment(10, date=str(today()+timedelta(days=1))).status_code, 400)
        self.assertEqual(self.payment(10, method='CREDIT').status_code, 400)
        response = self.payment(10)
        self.assertEqual(response.status_code, 200, response.data)
        with self.assertRaises(ValidationError):
            daybook_mutate(self.branch, self.owner, 'daybook-void', 'void', {'reason': 'Bypass'}, DaybookEntry.objects.get().pk)
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(f'{self.base}?outlet_id={self.branch.pk}').status_code, [401, 403])

    def test_inventory_supplier_registration_and_server_calculated_prices(self):
        item = inventory_item_create(self.branch, 'Oil', supplier_name='New oil supplier')
        self.assertIsNotNone(item.supplier_id)
        self.assertEqual(item.supplier.name, 'New oil supplier')
        invoice = self.bill(200, 20, subtotal=999, total_amount=999, due_amount=999)
        self.assertEqual(invoice.total_amount, Decimal('200'))
        self.assertEqual(invoice.due_amount, Decimal('180'))
        self.assertEqual(self.account()['summary']['amount_due'], Decimal('180'))

    def test_suppliers_without_bills_and_inactive_suppliers_are_listed(self):
        created = self.client.post(f'/api/v1/inventory/supplier-accounts/?outlet_id={self.branch.pk}',
            {'name': 'Advance supplier', 'credit_balance': '-50', 'is_active': False}, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        result = self.client.get(f'/api/v1/inventory/supplier-accounts/?outlet_id={self.branch.pk}')
        self.assertEqual(len(result.data['results']), 2)
        row = next(row for row in result.data['results'] if row['name'] == 'Advance supplier')
        self.assertEqual(row['advance'], Decimal('50'))
        self.assertEqual(row['invoice_count'], 0)

    def test_migration_preserves_opening_and_links_legacy_supplier_names(self):
        from importlib import import_module
        from django.apps import apps
        from django.db import connection
        from types import SimpleNamespace
        invoice = self.bill(100, 20)
        Supplier.objects.filter(pk=self.supplier.pk).update(credit_balance=130)
        PurchaseInvoice.objects.filter(pk=invoice.pk).update(initial_paid_amount=0)
        InventoryItem.objects.create(branch=self.branch, sku='LEGACY', name='Legacy stock', supplier_name='Legacy vendor')
        import_module('apps.inventory.migrations.0005_seed_supplier_accounts').seed(apps, SimpleNamespace(connection=connection))
        self.supplier.refresh_from_db()
        self.assertEqual(self.supplier.opening_balance, Decimal('50'))
        self.assertEqual(self.supplier.credit_balance, Decimal('130'))
        self.assertIsNotNone(InventoryItem.objects.get(sku='LEGACY').supplier_id)
