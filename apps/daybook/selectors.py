from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
from django.db.models import Q, Sum, Case, When, Value, IntegerField
from apps.payments.models import PaymentTransaction
from .models import DaybookEntry

LOCAL = ZoneInfo('Asia/Kathmandu')
ZERO = Decimal('0.00')


def entry_data(row):
    return {'staff_id': row.staff_id, 'id': row.pk, 'date': row.date.isoformat(), 'direction': row.direction, 'amount': str(row.amount),
        'payment_method': row.payment_method, 'category': row.category, 'party': row.party,
        'description': row.description, 'reference': row.reference, 'source': row.source,
        'recorded_by': row.recorded_by.get_full_name() or row.recorded_by.username,
        'created_at': row.created_at.isoformat(), 'voided_at': row.voided_at.isoformat() if row.voided_at else None,
        'voided_by': (row.voided_by.get_full_name() or row.voided_by.username) if row.voided_by else None,
        'void_reason': row.void_reason}


def amounts(rows):
    result = rows.aggregate(income=Sum('amount', filter=Q(direction='IN')), expense=Sum('amount', filter=Q(direction='OUT')))
    incoming, outgoing = result['income'] or ZERO, result['expense'] or ZERO
    return incoming, outgoing, incoming-outgoing


def ledger(branch, query, can_void):
    rows = DaybookEntry.objects.filter(branch=branch)
    if query.get('staff_only'):
        rows = rows.filter(staff__isnull=False)
    if query.get('staff_id'):
        rows = rows.filter(staff_id=query['staff_id'])
    active = rows.filter(voided_at__isnull=True)
    incoming, outgoing, net = amounts(active.filter(date=query['date']))
    opening = amounts(active.filter(date__lt=query['date']))[2]
    cash = active.filter(payment_method='CASH')
    cash_opening = amounts(cash.filter(date__lt=query['date']))[2]
    cash_in, cash_out, cash_net = amounts(cash.filter(date=query['date']))
    filtered = rows.filter(date=query['date']).select_related('recorded_by', 'voided_by')
    if not query['include_voided']:
        filtered = filtered.filter(voided_at__isnull=True)
    if query['direction'] != 'ALL':
        filtered = filtered.filter(direction=query['direction'])
    if query['payment_method'] != 'ALL':
        filtered = filtered.filter(payment_method=query['payment_method'])
    if query['search']:
        text = query['search']
        filtered = filtered.filter(Q(party__icontains=text) | Q(description__icontains=text) |
            Q(reference__icontains=text) | Q(category__icontains=text))
    size, page = query['page_size'], query['page']
    return {'date': query['date'].isoformat(), 'count': filtered.count(), 'page': page, 'page_size': size,
        'can_void': can_void,
        'summary': {key: format(value, '.2f') for key, value in {'opening': opening, 'income': incoming, 'expense': outgoing,
            'net': net, 'closing': opening+net, 'cash_opening': cash_opening, 'cash_in': cash_in,
            'cash_out': cash_out, 'cash_closing': cash_opening+cash_net}.items()},
        'results': [entry_data(row) for row in filtered.order_by('-created_at', '-pk')[(page-1)*size:page*size]]}


def importable_payments(branch, date, kind):
    start = datetime.combine(date, time.min, LOCAL)
    end = datetime.combine(date+timedelta(days=1), time.min, LOCAL)
    # Payment time matters: today's collection on an older order is today's IN.
    return PaymentTransaction.objects.filter(branch=branch, order__branch=branch,
        created_at__gte=start, created_at__lt=end, amount__gt=0,
        status='SUCCESS' if kind == 'SALE' else 'REFUNDED').exclude(payment_method='CREDIT')


def import_preview(branch, date, kind):
    payments = importable_payments(branch, date, kind).select_related('order', 'daybookentry').annotate(
        booked=Case(When(daybookentry__isnull=True, then=Value(0)), default=Value(1), output_field=IntegerField())
    ).order_by('booked', 'created_at', 'pk')
    count = payments.count()
    rows = []
    for payment in payments[:5000]:
        entry = getattr(payment, 'daybookentry', None)
        rows.append({'id': payment.pk, 'order_number': payment.order.order_number,
            'customer': payment.order.customer_name, 'source': payment.order.order_source,
            'amount': str(payment.amount), 'payment_method': payment.payment_method,
            'transaction_id': payment.transaction_id, 'created_at': payment.created_at.isoformat(),
            'imported': entry is not None, 'voided': bool(entry and entry.voided_at)})
    return {'date': date.isoformat(), 'kind': kind, 'count': count, 'results': rows,
            'truncated': count > 5000}


def outlet_staff(branch):
    from django.contrib.auth import get_user_model
    return get_user_model().objects.filter(
        Q(employee_profile__assigned_outlet=branch) |
        Q(employee_profile__isnull=True, branch=branch, role__in=['BRANCH_MANAGER', 'CHEF', 'WAITER', 'CASHIER', 'RIDER'])
    ).select_related('employee_profile').order_by('first_name', 'username', 'pk')


def staff_choices(branch):
    from django.contrib.auth import get_user_model
    current = outlet_staff(branch)
    historical = get_user_model().objects.filter(staff_daybook_entries__branch=branch).exclude(pk__in=current).distinct()
    result = []
    for user in [*current, *historical]:
        employee = getattr(user, 'employee_profile', None)
        assigned = employee.assigned_outlet_id == branch.pk if employee else user.branch_id == branch.pk
        active = assigned and user.is_active and (employee.is_active if employee else True)
        result.append({'id': user.pk, 'name': employee.name if employee else (user.get_full_name() or user.username or str(user.pk)), 'active': bool(active)})
    return result
