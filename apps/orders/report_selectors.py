"""Outlet-scoped sales aggregates. Never sum orders across an item join."""
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.db.models import Count, DecimalField, F, Max, Sum, Value
from django.db.models.functions import ExtractHour, Greatest, TruncDate

from .models import Order, OrderItem, OrderSource

LOCAL = ZoneInfo('Asia/Kathmandu')


def money(value):
    return str(Decimal(value or 0).quantize(Decimal('0.01')))


def sales_report(branch, filters):
    start, end = filters['start_date'], filters['end_date']
    days = (end - start).days + 1
    orders = Order.objects.filter(
        branch=branch, status='COMPLETED',
        created_at__gte=datetime.combine(start, time.min, LOCAL),
        created_at__lt=datetime.combine(end + timedelta(days=1), time.min, LOCAL),
    ).order_by().annotate(net_sales=Greatest(
        F('total_payable') - F('refunded_amount'),
        Value(Decimal('0.00')), output_field=DecimalField(max_digits=14, decimal_places=2),
    ))

    def totals(query):
        return query.annotate(orders=Count('pk'), sales=Sum('net_sales'))

    def row(value):
        return {**value, 'sales': money(value['sales']),
                'average_order_value': money(value['sales'] / value['orders'] if value['orders'] else 0)}

    summary = orders.aggregate(orders=Count('pk'), sales=Sum('net_sales'))
    summary = row({**summary, 'sales': summary['sales'] or Decimal('0')})
    summary.update(average_daily_sales=money(Decimal(summary['sales']) / days),
                   average_daily_orders=round(summary['orders'] / days, 2))
    daily = {r['date']: r for r in totals(orders.annotate(date=TruncDate('created_at', tzinfo=LOCAL)).values('date'))}
    timeline = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        timeline.append(row(daily.get(day, {'date': day, 'orders': 0, 'sales': Decimal('0')})))
    tables = [row(r) for r in totals(orders.filter(table_id__isnull=False).values(
        'table_id', 'table__table_number')).order_by('-sales', 'table_id')]
    channel_rows = {r['order_source']: row(r) for r in totals(orders.values('order_source'))}
    items = OrderItem.objects.filter(order_id__in=orders.values('pk'), is_voided=False).order_by()

    def top_items(query, limit, ranking):
        return [{**r, 'item_sales': money(r['item_sales'])} for r in query.values('product_id').annotate(
            name=Max('product_name'), quantity=Sum('quantity'), item_sales=Sum('line_total'),
        ).order_by(ranking, 'product_id')[:limit]]

    channels = []
    for source in OrderSource.values:
        channels.append({**channel_rows.get(source, {'order_source': source, 'orders': 0,
                        'sales': '0.00', 'average_order_value': '0.00'}),
                        'top_items': top_items(items.filter(order__order_source=source), 5, '-item_sales')})
    hourly_map = {r['hour']: r for r in totals(orders.annotate(hour=ExtractHour('created_at', tzinfo=LOCAL)).values('hour'))}
    hourly = []
    for hour in range(24):
        value = hourly_map.get(hour, {'hour': hour, 'orders': 0, 'sales': Decimal('0')})
        hourly.append({**row(value), 'average_orders': round(value['orders'] / days, 2),
                       'average_sales': money(value['sales'] / days)})
    return {'outlet_id': branch.pk, 'timezone': str(LOCAL), 'currency': 'NPR',
            'filters': filters, 'days': days, 'summary': summary, 'timeline': timeline,
            'tables': tables, 'channels': channels, 'top_items': top_items(items, 7, '-quantity'),
            'hourly': hourly}
