from datetime import timedelta
import logging
from asgiref.sync import async_to_sync
from celery import shared_task
from channels.layers import get_channel_layer
from django.db import transaction
from django.utils import timezone
from .models import MenuOutboxEvent

logger = logging.getLogger(__name__)


@shared_task(autoretry_for=(Exception,), retry_backoff=60, max_retries=3)
def publish_menu_events():
    """At-least-once delivery; clients deduplicate event_id and fetch current snapshots."""
    ids = list(MenuOutboxEvent.objects.filter(published_at__isnull=True,
        next_attempt_at__lte=timezone.now()).order_by('created_at').values_list('pk', flat=True)[:100])
    for event_id in ids:
        with transaction.atomic():
            event = MenuOutboxEvent.objects.select_for_update().get(pk=event_id)
            if event.published_at or event.next_attempt_at > timezone.now():
                continue
            try:
                layer = get_channel_layer()
                if layer is None:
                    raise RuntimeError('Channel layer is unavailable')
                payload = {'type': 'menu_updated', 'event_type': 'MENU_UPDATED', 'event_id': str(event.pk),
                    'aggregate_id': str(event.branch_id), 'outlet_id': event.branch_id, 'branch_id': event.branch_id,
                    'revision': event.revision, 'timestamp': event.created_at.isoformat()}
                async_to_sync(layer.group_send)(f'outlet_{event.branch_id}_menu', payload)
                event.published_at = timezone.now()
                event.last_error = ''
            except Exception as exc:
                event.attempts += 1
                event.last_error = str(exc)[:2000]
                event.next_attempt_at = timezone.now() + timedelta(seconds=min(300, 2 ** min(event.attempts, 8)))
                logger.exception('Menu event publication failed: %s', event.pk)
            event.save()
