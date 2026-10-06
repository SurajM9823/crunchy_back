import hashlib
import json
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError
from apps.orders.pos_services import Conflict
from apps.restaurants.models import Branch
from .models import DaybookEntry, DaybookEvent, DaybookMutation
from .selectors import entry_data, importable_payments
from .serializers import METHODS


@transaction.atomic
def mutate(branch, actor, key, action, data, entry_id=None):
    if not key or len(key) > 128:
        raise ValidationError('A valid Idempotency-Key is required.')
    Branch.objects.select_for_update().get(pk=branch.pk)
    fingerprint = hashlib.sha256(json.dumps([actor.pk, action, entry_id, data], sort_keys=True, default=str).encode()).hexdigest()
    previous = DaybookMutation.objects.filter(branch=branch, key=key).first()
    if previous:
        if previous.fingerprint != fingerprint:
            raise Conflict('This request key was already used for different entry details.')
        if previous.response.get('deleted'):
            raise Conflict('This entry was removed with its order.')
        return previous.response
    if action == 'create':
        row = DaybookEntry.objects.create(branch=branch, recorded_by=actor, **data)
        result = entry_data(row)
    elif action == 'void':
        row = DaybookEntry.objects.select_for_update().select_related('recorded_by', 'voided_by').filter(branch=branch, pk=entry_id).first()
        if not row:
            raise NotFound('Entry not found at this outlet.')
        if not row.voided_at:
            row.voided_at, row.voided_by, row.void_reason = timezone.now(), actor, data['reason']
            row.save(update_fields=['voided_at', 'voided_by', 'void_reason'])
        result = entry_data(row)
    elif action == 'import':
        ids = data['payment_ids']
        payments = list(importable_payments(branch, data['date'], data['kind']).select_for_update(of=('self',)).filter(pk__in=ids).select_related('order'))
        if len(payments) != len(ids):
            raise Conflict('Some payments are no longer eligible. Reload the import list and try again.')
        imported = set(DaybookEntry.objects.filter(payment_id__in=ids).values_list('payment_id', flat=True))
        new_rows = [DaybookEntry(branch=branch, date=data['date'], direction='IN' if data['kind']=='SALE' else 'OUT',
            amount=p.amount, payment_method=p.payment_method if p.payment_method in METHODS else 'OTHER',
            category='Sales received' if data['kind']=='SALE' else 'Sales refund',
            party=p.order.customer_name, description=f'{"Payment" if data["kind"]=="SALE" else "Refund"} for order {p.order.order_number}',
            reference=p.transaction_id, source=data['kind'], payment=p, recorded_by=actor)
            for p in payments if p.pk not in imported]
        DaybookEntry.objects.bulk_create(new_rows)
        result = {'created': len(new_rows), 'skipped': len(imported), 'date': data['date'].isoformat()}
    else:
        raise ValidationError('Unknown daybook action.')
    DaybookEvent.objects.create(branch=branch, event_type=f'DAYBOOK_{action.upper()}')
    DaybookMutation.objects.create(branch=branch, key=key, fingerprint=fingerprint, response=result)
    return result
