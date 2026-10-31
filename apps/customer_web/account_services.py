import hashlib
import json
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError
from apps.restaurants.models import Branch
from apps.orders.models import Order, PosSequence, OrderOutboxEvent
from apps.orders.pos_access import require_access
from apps.orders.pos_services import Conflict, record_tenders, receipt, audit
from apps.payments.models import PaymentTransaction
from apps.daybook.models import DaybookEntry, DaybookEvent
from .models import CustomerContact, CustomerCollection, CustomerAccountMutation
from .account_selectors import financial_orders


@transaction.atomic
def receive_customer_payment(branch, actor, contact_id, key, data):
    require_access(actor, branch, 'billing')
    if not key or not key.strip() or len(key) > 128:
        raise ValidationError('A valid Idempotency-Key is required.')
    branch = Branch.objects.select_for_update().select_related('restaurant').get(pk=branch.pk)
    fingerprint = hashlib.sha256(json.dumps([actor.pk, contact_id, data], sort_keys=True, default=str).encode()).hexdigest()
    previous = CustomerAccountMutation.objects.filter(branch=branch, key=key).first()
    if previous:
        if previous.fingerprint != fingerprint:
            raise Conflict('This request key was already used for different payment details.')
        return previous.response
    contact = CustomerContact.objects.filter(branch=branch, pk=contact_id).first()
    if not contact:
        raise NotFound('Customer not found at this outlet.')
    orders = list(financial_orders(branch, contact_id).select_for_update(of=('self',)).filter(account_due__gt=0)
                  .select_related('branch__restaurant').order_by('created_at', 'pk'))
    due = sum((order.account_due for order in orders), Decimal('0'))
    credit = sum((order.account_credit for order in orders), Decimal('0'))
    available = credit if data['scope'] == 'CREDIT' else due
    if data['expected_due'] != due or data['expected_credit'] != credit:
        raise Conflict('Customer balance changed. Review the latest account before receiving payment.')
    if data['amount'] > available:
        raise ValidationError('Payment exceeds the selected outstanding balance.')
    collection = CustomerCollection.objects.create(contact=contact, amount=data['amount'], method=data['method'],
        date=data['date'], reference=data['reference'], notes=data['notes'], recorded_by=actor)
    remaining = data['amount']
    allocations = []
    sequence, _ = PosSequence.objects.get_or_create(branch=branch)
    for order in orders:
        limit = order.account_credit if data['scope'] == 'CREDIT' else order.account_due
        applied = min(remaining, limit)
        if applied <= 0:
            continue
        # Recover legacy paid flags/transactions before calculating a new tender.
        order.paid_amount = order.account_paid
        record_tenders(order, [{'method': data['method'], 'amount': applied, 'reference': data['reference']}], actor)
        payment = PaymentTransaction.objects.filter(order=order, status='SUCCESS').latest('pk')
        payment.raw_response = {'customer_collection_id': collection.pk}
        payment.save(update_fields=['raw_response'])
        order.version += 1
        order.save()
        if order.order_source == 'WEBSITE':
            from .journey_services import sync_order_outcomes
            sync_order_outcomes(order)
        audit(order, actor, order.status, f'Customer receipt #{collection.pk}: {applied}')
        saved_receipt = receipt(order, 'BILL', sequence)
        DaybookEntry.objects.create(branch=branch, date=data['date'], direction='IN', amount=applied,
            payment_method=data['method'], category='Customer collection', party=contact.name,
            description=f'Customer receipt #{collection.pk}, order {order.order_number}. {data["notes"]}'[:1000],
            reference=data['reference'], source='SALE', payment=payment, recorded_by=actor)
        OrderOutboxEvent.objects.create(branch=branch, order=order, event_type='ORDER_SETTLE', payload={'version': order.version})
        allocations.append({'order_id': order.pk, 'order_number': order.order_number, 'amount': str(applied),
            'payment_id': payment.pk, 'receipt_id': saved_receipt.pk, 'receipt_number': saved_receipt.number})
        remaining -= applied
        if not remaining:
            break
    collection.snapshot = {'number': f'RCV-{branch.pk}-{collection.pk:08d}', 'customer_name': contact.name or 'Guest',
        'customer_phone': contact.phone, 'outlet': branch.name, 'seller': branch.restaurant.name,
        'date': str(data['date']), 'amount': str(data['amount']), 'method': data['method'], 'reference': data['reference'],
        'notes': data['notes'], 'recorded_by': actor.get_full_name() or actor.username,
        'remaining_due': str(due-data['amount']), 'allocations': allocations}
    collection.save(update_fields=['snapshot'])
    CustomerContact.objects.filter(pk=contact.pk).update(last_seen=timezone.now(), name=contact.name)
    DaybookEvent.objects.create(branch=branch, event_type='CUSTOMER_ACCOUNT_UPDATED')
    result = {'id': collection.pk, 'snapshot': collection.snapshot}
    CustomerAccountMutation.objects.create(branch=branch, key=key, fingerprint=fingerprint, response=result)
    return result


@transaction.atomic
def save_customer_discount(branch, actor, contact_id, values):
    from apps.orders.pos_access import require_access
    from rest_framework.exceptions import ValidationError
    require_access(actor, branch, 'discount')
    from django.shortcuts import get_object_or_404
    contact = get_object_or_404(CustomerContact.objects.select_for_update(), branch=branch, pk=contact_id)
    if values['discount_percent'] and not contact.phone:
        raise ValidationError('Save a customer phone number before assigning a discount.')
    contact.discount_percent = values['discount_percent']
    contact.discount_remark = values['discount_remark']
    contact.save(update_fields=['discount_percent', 'discount_remark'])


@transaction.atomic
def create_member(branch, actor, values, key):
    from .audience_services import remember_contact
    require_access(actor, branch, 'orders')
    if values['discount_percent'] or values['discount_remark']:
        require_access(actor, branch, 'discount')
    if not key or len(key) > 128:
        raise ValidationError('A valid request key is required.')
    fingerprint = hashlib.sha256(json.dumps({'action': 'create_member', **values}, sort_keys=True, default=str).encode()).hexdigest()
    Branch.objects.select_for_update().get(pk=branch.pk)
    previous = CustomerAccountMutation.objects.filter(branch=branch, key=key).first()
    if previous:
        if previous.fingerprint != fingerprint:
            raise Conflict('This request key was already used for different details.')
        return previous.response
    if CustomerContact.objects.filter(branch=branch, phone=values['phone']).exists():
        raise ValidationError('A customer with this phone already exists. Search for them to edit their discount.')
    contact = remember_contact(branch.pk, values['phone'], values['name'], 'MEMBER')
    contact.discount_percent = values['discount_percent']
    contact.discount_remark = values['discount_remark']
    contact.save(update_fields=['discount_percent', 'discount_remark'])
    result = {'id': contact.pk, 'name': contact.name, 'phone': contact.phone,
              'discount_percent': str(contact.discount_percent), 'discount_remark': contact.discount_remark}
    CustomerAccountMutation.objects.create(branch=branch, key=key, fingerprint=fingerprint, response=result)
    DaybookEvent.objects.create(branch=branch, event_type='CUSTOMER_ACCOUNT_UPDATED')
    return result


@transaction.atomic
def edit_customer_identity(branch, actor, contact_id, values):
    import re
    from django.shortcuts import get_object_or_404
    from .audience_services import contact_name, contact_phone
    require_access(actor, branch, 'orders')
    Branch.objects.select_for_update().get(pk=branch.pk)
    contact = get_object_or_404(CustomerContact.objects.select_for_update(), branch=branch, pk=contact_id)
    if contact.name != values['expected_name'] or contact.phone != values['expected_phone']:
        raise Conflict('Customer details changed. Refresh and try again.')
    name = contact_name(values.get('name', contact.name))
    raw_phone = values.get('phone', contact.phone)
    if raw_phone and (not re.fullmatch(r'[+\d\s().-]+', raw_phone) or len(re.sub(r'\D', '', raw_phone)) < 7):
        raise ValidationError('Enter a valid phone number.')
    phone = contact_phone(raw_phone)
    if not name and not phone:
        raise ValidationError('A customer needs a name or phone.')
    if not phone and (contact.discount_percent or Order.objects.filter(customer_contact=contact, credit_amount__gt=0).exists()):
        raise ValidationError('Customers with a saved discount or credit require a phone number.')
    identity = {'phone': phone} if phone else {'phone': '', 'name_key': name.casefold()}
    if CustomerContact.objects.filter(branch=branch, **identity).exclude(pk=contact.pk).exists():
        raise ValidationError('Another customer already uses these details.')
    contact.name, contact.phone, contact.name_key = name, phone, name.casefold() if not phone else ''
    contact.save(update_fields=['name', 'phone', 'name_key'])
    DaybookEvent.objects.create(branch=branch, event_type='CUSTOMER_ACCOUNT_UPDATED')
