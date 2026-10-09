from django.db.models import Count, Sum, Q, F
from apps.orders.models import Order
from .models import PostHogDelivery, PostHogOrderIdentity
from .posthog_services import project_config
from .journey_selectors import bounds


def reporting_overview(branch, filters, user):
    start, end = bounds(filters)
    orders = Order.objects.filter(branch=branch, order_source='WEBSITE', created_at__gte=start, created_at__lte=end)
    data = orders.aggregate(orders=Count('pk'), cancelled=Count('pk', filter=Q(status='CANCELLED')),
        order_value=Sum('total_payable', filter=~Q(status='CANCELLED')),
        paid_orders=Count('pk', filter=Q(payment_status='PAID', paid_amount__gt=0, paid_amount__gte=F('total_payable'))),
        received=Sum('paid_amount'), refunded=Sum('refunded_amount'))
    for field in ('order_value', 'received', 'refunded'):
        data[field] = str(data[field] or 0)
    data['net_received'] = str(sum((order.paid_amount-order.refunded_amount for order in orders), start=0))
    config = project_config(branch.pk)
    # A project link is never a substitute for authorization. Staff cannot see
    # project-wide analytics merely because they can read an outlet's sales.
    can_open = user.is_superuser or user.role in ('RESTAURANT_OWNER', 'BRANCH_MANAGER')
    delivery = PostHogDelivery.objects.filter(order__in=orders)
    return {'sales': data, 'analytics': {'provider': 'PostHog', 'configured': config['enabled'],
        'replay_enabled': config['replay'], 'project_url': config['project_url'] if can_open else '',
        'can_open': can_open, 'linked_orders': PostHogOrderIdentity.objects.filter(order__in=orders).count(),
        'pending_events': delivery.filter(status__in=['PENDING', 'SENDING']).count(),
        'failed_events': delivery.filter(status='FAILED').count(), 'sent_events': delivery.filter(status='SENT').count()},
        'scope': 'Website orders created in the selected Nepal-time range. Order value excludes cancellations; net received is recorded payments less refunds on these orders.'}
