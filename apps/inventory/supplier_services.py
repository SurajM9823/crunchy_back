"""Supplier account mutations; branch locks serialize purchases and payments."""
import hashlib
import json
from decimal import Decimal
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError
from apps.orders.pos_services import Conflict
from apps.restaurants.models import Branch
from apps.daybook.models import DaybookEntry, DaybookEvent
from .models import Supplier, SupplierPayment, SupplierMutation, PurchaseInvoice, DaybookAccountEntry


def reconcile_supplier(supplier):
    """Keep invoice settlement and net payable consistent, including advances."""
    invoices = list(PurchaseInvoice.objects.filter(supplier=supplier, branch_id=supplier.branch_id).order_by('purchase_date', 'created_at', 'pk'))
    payments = supplier.payments.filter(voided_at__isnull=True).aggregate(total=Sum('amount'))['total'] or Decimal('0')
    purchase_total = sum((row.total_amount for row in invoices), Decimal('0'))
    initial_paid = sum((row.initial_paid_amount for row in invoices), Decimal('0'))
    # Cash paid above a bill's value is an advance, as is a negative opening balance.
    pool = max(Decimal('0'), payments - supplier.opening_balance +
               sum((max(Decimal('0'), row.initial_paid_amount - row.total_amount) for row in invoices), Decimal('0')))
    for row in invoices:
        paid = min(row.total_amount, row.initial_paid_amount)
        allocated = min(pool, row.total_amount - paid)
        pool -= allocated
        row.paid_amount = paid + allocated
        row.due_amount = row.total_amount - row.paid_amount
        row.payment_status = 'PAID' if row.due_amount == 0 else 'PARTIAL' if row.paid_amount else 'PENDING'
    if invoices:
        PurchaseInvoice.objects.bulk_update(invoices, ['paid_amount', 'due_amount', 'payment_status'])
    supplier.credit_balance = supplier.opening_balance + purchase_total - initial_paid - payments
    supplier.save(update_fields=['credit_balance', 'updated_at'])


@transaction.atomic
def supplier_mutate(branch, actor, key, supplier_id, action, data, payment_id=None):
    if not key or not key.strip() or len(key) > 128:
        raise ValidationError('A valid Idempotency-Key is required.')
    Branch.objects.select_for_update().get(pk=branch.pk)
    fingerprint = hashlib.sha256(json.dumps([actor.pk, supplier_id, action, payment_id, data], sort_keys=True, default=str).encode()).hexdigest()
    previous = SupplierMutation.objects.filter(branch=branch, key=key).first()
    if previous:
        if previous.fingerprint != fingerprint:
            raise Conflict('This request key was used for different payment details.')
        return previous.response
    supplier = Supplier.objects.select_for_update().filter(branch=branch, pk=supplier_id).first()
    if not supplier:
        raise NotFound('Supplier not found at this outlet.')
    if action == 'payment':
        if data['expected_balance'] != supplier.credit_balance:
            raise Conflict('The supplier balance changed. Review the latest account before recording payment.')
        entry = DaybookEntry.objects.create(branch=branch, date=data['date'], direction='OUT',
            amount=data['amount'], payment_method=data['method'], category='Supplier payment',
            party=supplier.name, description=data['notes'] or f'Payment to {supplier.name}',
            reference=data['reference'], source='SUPPLIER', recorded_by=actor)
        payment = SupplierPayment.objects.create(supplier=supplier, recorded_by=actor, daybook_entry=entry,
            **{k: v for k, v in data.items() if k != 'expected_balance'})
        DaybookAccountEntry.objects.create(branch=branch, party=supplier, dr_amount=payment.amount,
            voucher_type='SUPPLIER_PAYMENT', narrative=f'Payment #{payment.pk}: {payment.reference}')
    elif action == 'void':
        payment = SupplierPayment.objects.select_for_update().filter(supplier=supplier, pk=payment_id).first()
        if not payment:
            raise NotFound('Payment not found for this supplier.')
        if payment.voided_at:
            raise Conflict('This payment has already been reversed.')
        payment.voided_at, payment.voided_by, payment.void_reason = timezone.now(), actor, data['reason']
        payment.save(update_fields=['voided_at', 'voided_by', 'void_reason'])
        DaybookEntry.objects.filter(pk=payment.daybook_entry_id).update(
            voided_at=payment.voided_at, voided_by=actor, void_reason=payment.void_reason)
        DaybookAccountEntry.objects.create(branch=branch, party=supplier, cr_amount=payment.amount,
            voucher_type='SUPPLIER_PAYMENT_VOID', narrative=f'Reversed payment #{payment.pk}: {payment.void_reason}')
    else:
        raise ValidationError('Unknown supplier action.')
    reconcile_supplier(supplier)
    DaybookEvent.objects.create(branch=branch, event_type='SUPPLIER_ACCOUNT_UPDATED')
    result = {'payment_id': payment.pk, 'balance': str(supplier.credit_balance)}
    SupplierMutation.objects.create(branch=branch, key=key, fingerprint=fingerprint, response=result)
    return result


@transaction.atomic
def update_supplier(branch, supplier_id, data):
    Branch.objects.select_for_update().get(pk=branch.pk)
    supplier = Supplier.objects.select_for_update().filter(branch=branch, pk=supplier_id).first()
    if not supplier:
        raise NotFound('Supplier not found at this outlet.')
    if 'name' in data and Supplier.objects.filter(branch=branch, name__iexact=data['name']).exclude(pk=supplier_id).exists():
        raise ValidationError({'name': 'A supplier with this name already exists.'})
    for key, value in data.items():
        setattr(supplier, key, value)
    supplier.save()
    DaybookEvent.objects.create(branch=branch, event_type='SUPPLIER_ACCOUNT_UPDATED')
    return supplier
