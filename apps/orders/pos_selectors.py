"""Bounded, outlet-scoped POS read models. Money stays decimal strings on the wire."""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from decimal import Decimal
from django.db.models import Q, Sum, Count, F, Prefetch
from .models import Order, OrderItem, PosReceipt

ACTIVE = ['PENDING', 'ACCEPTED', 'PREPARING', 'READY', 'OUT_FOR_DELIVERY']


def order_queryset(branch):
    return Order.objects.filter(branch=branch, is_pos_managed=True).select_related('table').prefetch_related(
        Prefetch('items',queryset=OrderItem.objects.order_by('round_number','id').prefetch_related('modifiers')),
        'payments', Prefetch('pos_receipts', queryset=PosReceipt.objects.defer('snapshot').order_by('pk')))


def order_data(order, detail=True):
    due = max(Decimal('0'), order.total_payable - order.paid_amount) if order.status != 'CANCELLED' else Decimal('0.00')
    settlement = 'REFUNDED' if order.refunded_amount else 'PAID' if due == 0 else 'CREDIT' if order.credit_amount else 'PARTIAL' if order.paid_amount else 'UNPAID'
    data = {key: getattr(order, key) for key in ['id', 'order_number', 'version', 'status', 'customer_name', 'customer_phone', 'fulfillment_type', 'order_source', 'payment_method', 'notes', 'delivery_address']}
    data.update({key: str(getattr(order, key)) for key in ['subtotal', 'total_payable', 'discount_amount', 'service_charge_amount', 'vat_included_amount', 'cash_round_down_savings', 'paid_amount', 'credit_amount', 'refunded_amount']})
    data.update(created_at=order.created_at.isoformat(), updated_at=order.updated_at.isoformat(), settlement=settlement,
                due_amount=str(due), unallocated_due=str(max(Decimal('0'), due-order.credit_amount)), table_id=order.table_id,
                table_number=order.table.table_number if order.table_id else None, discount_reason=order.discount_reason)
    data['items'] = [{'id': r.pk, 'product_id': r.product_id, 'product_name': r.product_name, 'variant_id': r.variant_id,
        'variant_name': r.variant_name, 'quantity': r.quantity, 'unit_price': str(r.unit_price), 'line_total': str(r.line_total),
        'requires_kitchen': r.requires_kitchen, 'kitchen_status': r.kitchen_status, 'round_number': r.round_number, 'item_notes': r.item_notes,
        'combo_components': r.combo_components, 'is_voided': r.is_voided, 'void_reason': r.void_reason,
        'modifiers': [{'name': m.option_name, 'group': m.group_name, 'price_delta': str(m.price_delta)} for m in r.modifiers.all()]} for r in order.items.all()]
    if detail:
        data['payments'] = [{'id': p.transaction_id, 'amount': str(p.amount), 'method': p.payment_method, 'status': p.status,
                            'reference': p.gateway_ref, 'created_at': p.created_at.isoformat()} for p in order.payments.all()]
        data['receipts'] = [{'id': p.pk, 'number': p.number, 'kind': p.kind, 'created_at': p.created_at.isoformat()} for p in order.pos_receipts.all()]
    return data


def list_orders(branch, filters):
    qs = order_queryset(branch)
    local = ZoneInfo('Asia/Kathmandu')
    if filters.get('start_date'):
        qs = qs.filter(created_at__gte=datetime.combine(filters['start_date'], time.min, local))
    if filters.get('end_date'):
        qs = qs.filter(created_at__lt=datetime.combine(filters['end_date'] + timedelta(days=1), time.min, local))
    if filters.get('active'):
        qs = qs.filter(Q(status__in=ACTIVE) | (Q(total_payable__gt=F('paid_amount')) & ~Q(status='CANCELLED')))
    if filters.get('open_tabs'):
        qs = qs.filter(status__in=ACTIVE)
    if filters.get('kitchen'):
        qs = qs.filter(status__in=['PENDING','ACCEPTED','PREPARING','READY'], items__requires_kitchen=True, items__is_voided=False).distinct()
    if filters.get('fulfillment', 'ALL') != 'ALL':
        qs = qs.filter(fulfillment_type=filters['fulfillment'])
    if filters.get('search'):
        s = filters['search']
        qs = qs.filter(Q(order_number__icontains=s) | Q(customer_name__icontains=s) | Q(customer_phone__icontains=s) | Q(items__product_name__icontains=s)).distinct()
    settlement = filters.get('settlement', 'ALL')
    if settlement == 'PAID': qs = qs.filter(paid_amount__gte=F('total_payable'), refunded_amount=0)
    if settlement == 'UNPAID': qs = qs.filter(paid_amount=0, credit_amount=0, total_payable__gt=0).exclude(status='CANCELLED')
    if settlement == 'PARTIAL': qs = qs.filter(paid_amount__gt=0, paid_amount__lt=F('total_payable'), credit_amount=0).exclude(status='CANCELLED')
    if settlement == 'CREDIT': qs = qs.filter(credit_amount__gt=0)
    counts = {r['status']: r['n'] for r in qs.order_by().values('status').annotate(n=Count('id', distinct=True))}
    if filters.get('status', 'ALL') != 'ALL': qs = qs.filter(status=filters['status'])
    # Aggregate over a deduplicated ID subquery, never fan out across item/payment joins.
    base = Order.objects.filter(pk__in=qs.values('pk'))
    sums = base.exclude(status='CANCELLED').aggregate(gross=Sum('subtotal'), discount=Sum('discount_amount'), final=Sum('total_payable'), paid=Sum('paid_amount'), credit=Sum('credit_amount'))
    sums['refunded'] = base.aggregate(v=Sum('refunded_amount'))['v']
    summary = {k: str(v or Decimal('0.00')) for k, v in sums.items()}
    summary['due'] = str((sums['final'] or 0) - (sums['paid'] or 0))
    from apps.payments.models import PaymentTransaction
    summary['methods'] = {r['payment_method']: str(r['amount']) for r in PaymentTransaction.objects.filter(order_id__in=base.values('pk'), status='SUCCESS').order_by().values('payment_method').annotate(amount=Sum('amount'))}
    count = base.count()
    size = filters.get('page_size',25)
    page = filters.get('page',1)
    rows = qs.order_by('created_at','id')[(page-1)*size:page*size] if filters.get('kitchen') else qs.order_by('-created_at','-id')[(page-1)*size:page*size]
    return {'results': [order_data(o) for o in rows], 'count': count, 'page': page, 'page_size': size, 'summary': summary, 'status_counts': counts}
