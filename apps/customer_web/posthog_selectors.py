import json
import logging
from datetime import timedelta, timezone as datetime_timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
from django.conf import settings
from django.core.cache import cache
from django.db.models import Count, Sum, Q, F
from django.utils import timezone
from apps.orders.models import Order
from .models import PostHogDelivery, PostHogOrderIdentity
from .posthog_services import project_config
from .journey_selectors import bounds

logger = logging.getLogger(__name__)
NEPAL_TIMEZONE = ZoneInfo('Asia/Kathmandu')

VISITOR_ACTIONS = (
    'add_to_cart', 'remove_from_cart', 'cart_clear', 'cart_view',
    'category_view', 'menu_view', 'product_view', 'product_detail_view',
    'product_image_view', 'product_search', 'checkout_click', 'checkout_start',
    'checkout_validation_failed', 'confirm_order_click', 'delivery_information',
    'address_entered', 'location_selected', 'map_opened', 'map_pin_selected',
    'delivery_location_error', 'name_entered', 'phone_entered',
    'auth_required', 'login_started', 'login_success', 'login_failed',
    'payment_method_view', 'payment_method_selected',
    'payment_proof_uploaded', 'payment_started', 'order_submit', 'order_failed',
    'payment_proof_rejected',
    'api_error', 'network_error', 'javascript_error', 'image_error',
    'call_click', 'whatsapp_click',
)

FUNNEL_STAGES = (
    ('Visit', 'visit_at > epoch'),
    ('Product viewed', 'visit_at > epoch AND product_at >= visit_at'),
    ('Added to cart', 'visit_at > epoch AND product_at >= visit_at AND cart_at >= product_at'),
    ('Checkout started', 'visit_at > epoch AND product_at >= visit_at AND cart_at >= product_at AND checkout_at >= cart_at'),
    ('Payment details shown', 'visit_at > epoch AND product_at >= visit_at AND cart_at >= product_at AND checkout_at >= cart_at AND payment_at >= checkout_at'),
    ('Receipt selected', 'visit_at > epoch AND product_at >= visit_at AND cart_at >= product_at AND checkout_at >= cart_at AND payment_at >= checkout_at AND receipt_at >= payment_at'),
    ('Order attempted', 'visit_at > epoch AND product_at >= visit_at AND cart_at >= product_at AND checkout_at >= cart_at AND payment_at >= checkout_at AND receipt_at >= payment_at AND submit_at >= receipt_at'),
)

FUNNEL_EVENTS = tuple(
    event
    for event in (
        '$pageview', 'product_view', 'product_detail_view', 'add_to_cart',
        'checkout_start', 'payment_method_view', 'payment_proof_uploaded',
        'order_submit',
    )
)

LAST_STEP_EVENTS = tuple(event for event in VISITOR_ACTIONS if event not in {
    'api_error', 'network_error', 'javascript_error', 'image_error',
})

FRICTION_EVENTS = (
    'login_started', 'login_success', 'login_failed', 'auth_required',
    'map_opened', 'location_selected', 'delivery_location_error',
    'payment_method_view', 'payment_method_selected', 'payment_proof_uploaded', 'payment_proof_rejected',
    'checkout_validation_failed', 'order_failed', 'api_error', 'network_error',
    'javascript_error', 'image_error',
)

FRICTION_CATEGORIES = {
    'name_missing', 'address_missing', 'table_missing', 'cart_sync',
    'location_unavailable', 'location_invalid', 'location_permission', 'location_timeout',
    'unsupported_type', 'file_too_large', 'empty_file',
    'image_load', 'uncaught_exception', 'unhandled_rejection', 'network',
}


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

    cache_key = f'posthog-overview:v2:{branch.pk}:{config["project_url"]}:{start.isoformat()}:{end.isoformat()}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    start_utc = start.astimezone(datetime_timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')
    end_utc = end.astimezone(datetime_timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')
    month_now = timezone.now().astimezone(NEPAL_TIMEZONE)
    month_start = month_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_start_utc = month_start.astimezone(datetime_timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')
    month_now_utc = month_now.astimezone(datetime_timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')
    scope = f"toString(properties.outlet_id) = '{branch.pk}' AND properties.authority = 'browser'"
    time_filter = f"timestamp >= toDateTime64('{start_utc}', 6, 'UTC') AND timestamp <= toDateTime64('{end_utc}', 6, 'UTC')"
    month_time_filter = f"timestamp >= toDateTime64('{month_start_utc}', 6, 'UTC') AND timestamp <= toDateTime64('{month_now_utc}', 6, 'UTC')"
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
                toDate(toTimeZone(timestamp, 'Asia/Kathmandu')) AS date,
                uniqExact(distinct_id) AS visitors,
                uniqExact(properties.$session_id) AS sessions,
                count() AS page_views
            FROM events
            WHERE {month_time_filter} AND {scope} AND event = '$pageview'
            GROUP BY date
            ORDER BY date
        """)
        actions = _posthog_rows(config, f"""
            SELECT event, count() AS events, countDistinct(distinct_id) AS visitors
            FROM events
            WHERE {time_filter} AND {scope}
              AND event IN ({', '.join(repr(event) for event in VISITOR_ACTIONS)})
            GROUP BY event
            ORDER BY events DESC
            LIMIT 25
        """)
        pages = _posthog_rows(config, f"""
            SELECT properties.path AS path, count() AS page_views, countDistinct(distinct_id) AS visitors
            FROM events
            WHERE {time_filter} AND {scope} AND event = '$pageview'
            GROUP BY path
            ORDER BY page_views DESC
            LIMIT 10
        """)
        funnel = _posthog_rows(config, f"""
            SELECT
                {', '.join(f'countIf({condition})' for _, condition in FUNNEL_STAGES)}
            FROM (
                SELECT
                    properties.$session_id AS session_id,
                    minIf(timestamp, event = '$pageview') AS visit_at,
                    minIf(timestamp, event IN ('product_view', 'product_detail_view')) AS product_at,
                    minIf(timestamp, event = 'add_to_cart') AS cart_at,
                    minIf(timestamp, event = 'checkout_start') AS checkout_at,
                    minIf(timestamp, event = 'payment_method_view') AS payment_at,
                    minIf(timestamp, event = 'payment_proof_uploaded') AS receipt_at,
                    minIf(timestamp, event = 'order_submit') AS submit_at,
                    toDateTime64('1970-01-01 00:00:00', 6, 'UTC') AS epoch
                FROM events
                WHERE {time_filter} AND {scope}
                  AND event IN ({', '.join(repr(event) for event in FUNNEL_EVENTS)})
                  AND properties.$session_id != ''
                GROUP BY session_id
            )
        """)
        last_steps = _posthog_rows(config, f"""
            SELECT last_step, count() AS sessions, uniqExact(visitor_id) AS visitors
            FROM (
                SELECT
                    properties.$session_id AS session_id,
                    any(distinct_id) AS visitor_id,
                    argMaxIf(event, timestamp, event IN ({', '.join(repr(event) for event in LAST_STEP_EVENTS)})) AS last_step,
                    max(timestamp) AS last_seen
                FROM events
                WHERE {time_filter} AND {scope} AND properties.$session_id != ''
                GROUP BY session_id
                HAVING last_seen < now() - INTERVAL 30 MINUTE
            )
            WHERE last_step != ''
            GROUP BY last_step
            ORDER BY sessions DESC
            LIMIT 15
        """)
        friction = _posthog_rows(config, f"""
            SELECT event, properties.error_category AS category, count() AS events,
                   uniqExact(properties.$session_id) AS sessions
            FROM events
            WHERE {time_filter} AND {scope}
              AND event IN ({', '.join(repr(event) for event in FRICTION_EVENTS)})
            GROUP BY event, category
            ORDER BY events DESC
        """)
        if not totals or len(totals[0]) < 3:
            raise ValueError('PostHog returned incomplete visitor totals.')
        funnel_counts = funnel[0] if funnel else [0] * len(FUNNEL_STAGES)
        if len(funnel_counts) < len(FUNNEL_STAGES):
            raise ValueError('PostHog returned incomplete funnel metrics.')
        daily_by_date = {
            str(row[0]): {'date': str(row[0]), 'visitors': int(row[1] or 0), 'sessions': int(row[2] or 0), 'page_views': int(row[3] or 0)}
            for row in daily if len(row) >= 4
        }
        daily_month = []
        for day_offset in range((month_now.date() - month_start.date()).days + 1):
            date = str(month_start.date() + timedelta(days=day_offset))
            daily_month.append(daily_by_date.get(date, {'date': date, 'visitors': 0, 'sessions': 0, 'page_views': 0}))
        result = {
            'available': True,
            'message': '',
            'unique_visitors': int(totals[0][0] or 0),
            'sessions': int(totals[0][1] or 0),
            'page_views': int(totals[0][2] or 0),
            'daily': daily_month,
            'top_events': [
                {'event': str(row[0]), 'events': int(row[1] or 0), 'visitors': int(row[2] or 0)}
                for row in actions if len(row) >= 3
            ],
            'top_pages': [
                {'path': str(row[0] or '/'), 'page_views': int(row[1] or 0), 'visitors': int(row[2] or 0)}
                for row in pages if len(row) >= 3
            ],
            'funnel': [
                {'step': label, 'sessions': int(funnel_counts[index] or 0)}
                for index, (label, _) in enumerate(FUNNEL_STAGES)
            ],
            'last_steps': [
                {'event': str(row[0]), 'sessions': int(row[1] or 0), 'visitors': int(row[2] or 0)}
                for row in last_steps if len(row) >= 3
            ],
            'friction': [
                {
                    'event': str(row[0]),
                    'category': _safe_friction_category(row[1]),
                    'events': int(row[2] or 0),
                    'sessions': int(row[3] or 0),
                }
                for row in friction if len(row) >= 4
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


def _safe_friction_category(value):
    category = str(value or '')
    if category in FRICTION_CATEGORIES or (
        category.startswith('http_')
        and len(category) == 8
        and category[5:].isdigit()
        and 400 <= int(category[5:]) <= 599
    ):
        return category
    return 'other' if category else ''


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
