import json
import logging
from datetime import timezone as datetime_timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from django.conf import settings
from django.core.cache import cache
from django.db.models import Count, Sum, Q, F
from apps.orders.models import Order
from .models import PostHogDelivery, PostHogOrderIdentity
from .posthog_services import project_config
from .journey_selectors import bounds

logger = logging.getLogger(__name__)


def _posthog_rows(config, query):
    import re

    match = re.fullmatch(r'https://(?:us|eu)\.posthog\.com/project/(\d+)/?', config['project_url'])
    if not match:
        raise ValueError('PostHog project URL is unavailable.')
    payload = json.dumps({'query': {'kind': 'HogQLQuery', 'query': query}}).encode()
    request = Request(
        f"{config['host'].replace('.i.posthog.com', '.posthog.com')}/api/projects/{match.group(1)}/query/",
        data=payload,
        headers={
            'Authorization': f'Bearer {settings.POSTHOG_QUERY_API_KEY}',
            'Content-Type': 'application/json',
        },
        method='POST',
    )
    with urlopen(request, timeout=12) as response:
        if not 200 <= response.status < 300:
            raise ValueError('PostHog returned an unexpected response.')
        result = json.loads(response.read(2_000_000))
    if not isinstance(result, dict) or not isinstance(result.get('results'), list):
        raise ValueError('PostHog returned an invalid query response.')
    return result['results']


def visitor_overview(branch, config, start, end):
    if not config['enabled']:
        return {'available': False, 'message': 'PostHog is not configured for this outlet.'}
    if not settings.POSTHOG_QUERY_API_KEY:
        return {
            'available': False,
            'message': 'Add a read-only PostHog Personal API Key to the backend as POSTHOG_QUERY_API_KEY to show visitor charts here.',
        }

    cache_key = f'posthog-overview:{branch.pk}:{config["project_url"]}:{start.isoformat()}:{end.isoformat()}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    start_utc = start.astimezone(datetime_timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')
    end_utc = end.astimezone(datetime_timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')
    scope = f"properties.outlet_id = '{branch.pk}' AND properties.authority = 'browser'"
    time_filter = f"timestamp >= toDateTime64('{start_utc}', 6, 'UTC') AND timestamp <= toDateTime64('{end_utc}', 6, 'UTC')"
    try:
        totals = _posthog_rows(config, f"""
            SELECT
                uniqExactIf(distinct_id, event = '$pageview') AS visitors,
                uniqExactIf(properties.$session_id, event = '$pageview') AS sessions,
                sumIf(1, event = '$pageview') AS page_views
            FROM events
            WHERE {time_filter} AND {scope}
        """)
        daily = _posthog_rows(config, f"""
            SELECT
                toDate(timestamp, 'Asia/Kathmandu') AS date,
                uniqExact(distinct_id) AS visitors,
                uniqExact(properties.$session_id) AS sessions,
                count() AS page_views
            FROM events
            WHERE {time_filter} AND {scope} AND event = '$pageview'
            GROUP BY date
            ORDER BY date
        """)
        actions = _posthog_rows(config, f"""
            SELECT event, count() AS events, countDistinct(distinct_id) AS visitors
            FROM events
            WHERE {time_filter} AND {scope} AND event != '$pageview'
            GROUP BY event
            ORDER BY events DESC
            LIMIT 10
        """)
        pages = _posthog_rows(config, f"""
            SELECT properties.path AS path, count() AS page_views, countDistinct(distinct_id) AS visitors
            FROM events
            WHERE {time_filter} AND {scope} AND event = '$pageview'
            GROUP BY path
            ORDER BY page_views DESC
            LIMIT 10
        """)
        if not totals or len(totals[0]) < 3:
            raise ValueError('PostHog returned incomplete visitor totals.')
        result = {
            'available': True,
            'message': '',
            'unique_visitors': int(totals[0][0] or 0),
            'sessions': int(totals[0][1] or 0),
            'page_views': int(totals[0][2] or 0),
            'daily': [
                {'date': str(row[0]), 'visitors': int(row[1] or 0), 'sessions': int(row[2] or 0), 'page_views': int(row[3] or 0)}
                for row in daily if len(row) >= 4
            ],
            'top_events': [
                {'event': str(row[0]), 'events': int(row[1] or 0), 'visitors': int(row[2] or 0)}
                for row in actions if len(row) >= 3
            ],
            'top_pages': [
                {'path': str(row[0] or '/'), 'page_views': int(row[1] or 0), 'visitors': int(row[2] or 0)}
                for row in pages if len(row) >= 3
            ],
        }
    except HTTPError as exc:
        logger.warning('PostHog visitor query failed with HTTP %s for outlet %s', exc.code, branch.pk)
        message = 'PostHog denied the analytics query. Check that the backend key has read access to this project.' if exc.code in (401, 403) else 'PostHog visitor analytics is temporarily unavailable.'
        return {'available': False, 'message': message}
    except (URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        logger.warning('PostHog visitor query failed for outlet %s (%s)', branch.pk, type(exc).__name__)
        return {'available': False, 'message': 'PostHog visitor analytics is temporarily unavailable. Check backend logs and API-key permissions.'}

    cache.set(cache_key, result, timeout=120)
    return result


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
    visitor_metrics = visitor_overview(branch, config, start, end)
    return {'sales': data, 'analytics': {'provider': 'PostHog', 'configured': config['enabled'],
        'replay_enabled': config['replay'], 'project_url': config['project_url'] if can_open else '',
        'can_open': can_open, 'linked_orders': PostHogOrderIdentity.objects.filter(order__in=orders).count(),
        'pending_events': delivery.filter(status__in=['PENDING', 'SENDING']).count(),
        'failed_events': delivery.filter(status='FAILED').count(), 'sent_events': delivery.filter(status='SENT').count(),
        'visitors': visitor_metrics},
        'scope': 'Website orders created in the selected Nepal-time range. Order value excludes cancellations; net received is recorded payments less refunds on these orders.'}
