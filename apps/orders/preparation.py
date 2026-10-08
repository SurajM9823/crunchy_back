"""Preparation rounds preserve the progress of earlier additions to an order."""
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from apps.tables.models import DiningTable


def rounds(order):
    grouped = {}
    for item in order.items.all():
        if not item.is_voided:
            grouped.setdefault(item.round_number, []).append(item)
    result = []
    for number, items in sorted(grouped.items()):
        states = {item.kitchen_status for item in items}
        status = 'SERVED' if states == {'SERVED'} else 'WAITING' if states <= {'WAITING'} else 'PREPARING' if states & {'WAITING','PREPARING'} else 'READY'
        first = lambda field: min((getattr(item, field) for item in items if getattr(item, field)), default=None)
        result.append({'number':number,'status':status,'item_ids':[item.pk for item in items],
            'created_at':first('created_at').isoformat(),
            **{field: first(field).isoformat() if first(field) else None for field in ['preparation_started_at','ready_at','served_at']}})
    return result


def append_allowed(order):
    return (order.status in ('ACCEPTED','PREPARING','READY','COMPLETED')
            and order.order_source != 'WEBSITE'
            and not (order.billed_at or order.paid_amount or order.credit_amount))


def sync_status(order):
    states = {item.kitchen_status for item in order.items.filter(is_voided=False)}
    if states == {'SERVED'}:
        order.status = 'COMPLETED'
        if order.table_id:
            DiningTable.objects.filter(pk=order.table_id, active_session_id=order.table_session_id).update(active_session_id=None)
    elif states & {'WAITING','PREPARING'}:
        order.status = 'ACCEPTED' if states == {'WAITING'} else 'PREPARING'
    elif states:
        order.status = 'READY'


def advance_round(order, number, target):
    if order.status not in ('ACCEPTED','PREPARING','READY'):
        raise ValidationError('This order cannot change preparation rounds now.')
    current = next((row for row in rounds(order) if row['number'] == number), None)
    expected = {'PREPARING':'WAITING','READY':'PREPARING','SERVED':'READY'}
    if not current or current['status'] != expected[target]:
        raise ValidationError('This round has changed. Refresh its current status.')
    if target == 'SERVED' and order.fulfillment_type == 'DELIVERY':
        raise ValidationError('Dispatch delivery orders together after every round is ready.')
    field = {'PREPARING':'preparation_started_at','READY':'ready_at','SERVED':'served_at'}[target]
    order.items.filter(pk__in=current['item_ids']).update(kitchen_status=target, **{field:timezone.now()})
    sync_status(order)


def call_round(order, number=None):
    ready = [row for row in rounds(order) if row['status'] == 'READY']
    if number is not None:
        ready = [row for row in ready if row['number'] == number]
    if not ready or order.status not in ('ACCEPTED','PREPARING','READY'):
        raise ValidationError('Only an unserved ready round can be called.')
    if number is None and len(ready) > 1:
        raise ValidationError('Choose which ready round to call.')
    return ready[0]['number']
