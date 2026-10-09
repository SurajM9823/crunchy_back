"""PostHog configuration and transactional order outbox. Never call PostHog in HTTP."""
import re
import uuid
from urllib.parse import urlsplit
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from .models import PostHogOrderIdentity, PostHogDelivery


def project_config(branch_id):
    # Each outlet must be explicitly mapped. A missing mapping never falls back to
    # another outlet's project, and project access is managed in PostHog itself.
    config = settings.POSTHOG_OUTLETS.get(str(branch_id), {})
    token = config.get('token', '')
    host = config.get('host', '')
    url = config.get('project_url', '')
    valid = bool(re.fullmatch(r'phc_[A-Za-z0-9_-]+', token)) and host in (
        'https://us.i.posthog.com', 'https://eu.i.posthog.com')
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.netloc not in ('us.posthog.com', 'eu.posthog.com') or not re.fullmatch(r'/project/\d+/?', parsed.path):
        url = ''
    return {'enabled': valid, 'token': token if valid else '', 'host': host if valid else '',
            'project_url': url if valid else '', 'replay': valid and config.get('replay', False) is True}


def distinct_id(branch_id, visitor_id):
    return f'outlet-{branch_id}:{visitor_id}'


@transaction.atomic
def attach_posthog_order(order, context):
    if not context or not context.get('posthog_session_id') or not project_config(order.branch_id)['enabled']:
        return
    identity, _ = PostHogOrderIdentity.objects.get_or_create(order=order, defaults={
        'visitor_id': context['visitor_id'], 'session_id': context['posthog_session_id']})
    enqueue_order_event(order, identity, 'order_success')


def enqueue_order_event(order, identity, name):
    config = project_config(order.branch_id)
    if not config['enabled']:
        return
    event_id = uuid.uuid4()
    properties = {'distinct_id': distinct_id(order.branch_id, identity.visitor_id),
        '$session_id': str(identity.session_id), '$insert_id': str(event_id),
        '$process_person_profile': False, '$geoip_disable': True,
        'event_id': str(event_id), 'event_type': name, 'aggregate_id': str(order.pk),
        'outlet_id': str(order.branch_id), 'order_id': str(order.pk), 'authority': 'server',
        'currency': 'NPR', 'order_value': str(order.total_payable),
        'paid_amount': str(order.paid_amount), 'payment_method': order.payment_method,
        'order_status': order.status, 'fulfillment_type': order.fulfillment_type}
    PostHogDelivery.objects.get_or_create(order=order, event=name, defaults={
        'id': event_id, 'project_token': config['token'], 'api_host': config['host'],
        'payload': {'uuid': str(event_id), 'event': name, 'timestamp': timezone.now().isoformat(), 'properties': properties}})


@transaction.atomic
def sync_posthog_order(order):
    identity = PostHogOrderIdentity.objects.filter(order=order).first()
    if not identity:
        return
    if order.status not in ('PENDING', 'CANCELLED'):
        enqueue_order_event(order, identity, 'order_confirmed')
    if order.payment_status == 'PAID' and order.paid_amount > 0 and order.paid_amount >= order.total_payable:
        enqueue_order_event(order, identity, 'payment_success')
