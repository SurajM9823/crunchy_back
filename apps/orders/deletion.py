"""Atomic removal of an order and its dependent billing records."""
import hashlib
import json
from django.db import transaction
from django.db.models import Sum
from rest_framework.exceptions import NotFound, ValidationError
from apps.restaurants.models import Branch
from apps.tables.models import DiningTable
from apps.payments.models import FiscalInvoice, PaymentTransaction
from apps.customer_web.models import CustomerOrder
from apps.daybook.models import DaybookEntry, DaybookEvent, DaybookMutation
from apps.inventory.models import InventoryItem, StockTransaction, StockMovementLedger
from .models import Order, PosReceipt, PosCreditEntry, PosMutation, OrderOutboxEvent
from .pos_access import require_access
from .pos_services import Conflict


def deleted_web_key(user_id, key):
    return 'deleted-web:' + hashlib.sha256(f'{user_id}:{key}'.encode()).hexdigest()


@transaction.atomic
def delete_order(branch, actor, key, order_id, data):
    require_access(actor, branch, 'delete_order')
    if not key or len(key) > 128:
        raise ValidationError('A valid Idempotency-Key is required.')
    fingerprint = hashlib.sha256(json.dumps([actor.pk, 'delete', order_id, data], sort_keys=True).encode()).hexdigest()
    branch = Branch.objects.select_for_update().get(pk=branch.pk)
    previous = PosMutation.objects.filter(branch=branch, key=key).first()
    if previous:
        if previous.fingerprint != fingerprint:
            raise Conflict('This request key belongs to another action.')
        return previous.response
    order = Order.objects.select_for_update().filter(branch=branch, pk=order_id).first()
    if not order:
        raise NotFound('Order not found at this outlet.')
    if order.version != data['version']:
        raise Conflict()
    if data['confirmation'] != order.order_number:
        raise ValidationError('Enter the exact order number to confirm deletion.')

    customer = CustomerOrder.objects.filter(order=order).first()
    owner_id = customer.user_id if customer else None
    if customer:
        # Keep only an opaque retry marker, never the receipt image or order snapshot.
        PosMutation.objects.get_or_create(branch=branch, key=deleted_web_key(owner_id, customer.request_key),
                                          defaults={'fingerprint': '', 'response': {'deleted': True}})
    stock = StockTransaction.objects.filter(reference_order=order)
    if data['restore_stock']:
        deltas = {r['inventory_item_id']: r['total'] for r in
                  stock.values('inventory_item_id').annotate(total=Sum('quantity'))}
        for item in InventoryItem.objects.select_for_update().filter(branch=branch, pk__in=deltas).order_by('pk'):
            item.current_stock -= deltas[item.pk]
            item.save(update_fields=['current_stock', 'updated_at'])
    stock.delete()
    StockMovementLedger.objects.filter(branch=branch, reference_id=order.order_number,
                                        reason__in=['SALE_DEDUCTION', 'POS_VOID']).delete()
    entries = DaybookEntry.objects.filter(payment__order=order)
    DaybookMutation.objects.filter(branch=branch, response__id__in=list(entries.values_list('pk', flat=True))).update(response={'deleted': True})
    entries.delete()
    FiscalInvoice.objects.filter(order=order).delete()
    PaymentTransaction.objects.filter(order=order).delete()
    PosReceipt.objects.filter(order=order).delete()
    PosCreditEntry.objects.filter(order=order).delete()
    CustomerOrder.objects.filter(order=order).delete()
    # Scrub cached snapshots but retain retry keys so a stale retry cannot recreate the order.
    PosMutation.objects.filter(branch=branch, response__id=order_id,
                               response__order_number=order.order_number).update(response={'deleted': True})
    if order.table_id and not Order.objects.filter(table_id=order.table_id,
            table_session_id=order.table_session_id).exclude(pk=order.pk).exclude(status__in=['COMPLETED', 'CANCELLED']).exists():
        DiningTable.objects.filter(pk=order.table_id, active_session_id=order.table_session_id).update(active_session_id=None)
    order.delete()  # Items, modifiers, status history, loyalty purchases and old outbox events.
    # Minimal durable invalidation survives deletion; contains no receipt or financial details.
    OrderOutboxEvent.objects.create(branch=branch, event_type='ORDER_DELETED',
                                   payload={'aggregate_id': order_id, 'customer_id': owner_id})
    DaybookEvent.objects.create(branch=branch, event_type='ORDER_DELETED')
    if data['restore_stock']:
        from apps.catalog.services import menu_changed
        menu_changed(branch_id=branch.pk)
    response = {'deleted': True, 'order_id': order_id}
    PosMutation.objects.create(branch=branch, key=key, fingerprint=fingerprint, response=response)
    return response
