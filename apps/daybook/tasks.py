import logging
from datetime import timedelta
from asgiref.sync import async_to_sync
from celery import shared_task
from channels.layers import get_channel_layer
from django.db import transaction
from django.utils import timezone
from .models import DaybookEvent

logger = logging.getLogger(__name__)


@shared_task(autoretry_for=(Exception,), retry_backoff=60, max_retries=3)
def publish_daybook_events():
    ids = list(DaybookEvent.objects.filter(published_at__isnull=True, next_attempt_at__lte=timezone.now()).order_by('pk').values_list('pk', flat=True)[:100])
    for pk in ids:
        with transaction.atomic():
            event = DaybookEvent.objects.select_for_update().get(pk=pk)
            if event.published_at or event.next_attempt_at > timezone.now():
                continue
            try:
                layer = get_channel_layer()
                if layer is None:
                    raise RuntimeError('Channel layer unavailable')
                async_to_sync(layer.group_send)(f'daybook_{event.branch_id}', {'type': 'daybook_event',
                    'event_type': event.event_type, 'event_id': str(event.event_id), 'outlet_id': event.branch_id,
                    'aggregate_id': event.branch_id, 'timestamp': event.created_at.isoformat()})
                if event.event_type == 'SUPPLIER_ACCOUNT_UPDATED':
                    async_to_sync(layer.group_send)(f'suppliers_{event.branch_id}', {'type': 'supplier_event',
                        'event_type': event.event_type, 'event_id': str(event.event_id), 'outlet_id': event.branch_id,
                        'aggregate_id': event.branch_id, 'timestamp': event.created_at.isoformat()})
                if event.event_type == 'CUSTOMER_ACCOUNT_UPDATED':
                    async_to_sync(layer.group_send)(f'customer_accounts_{event.branch_id}', {'type': 'account_event',
                        'event_type': event.event_type, 'event_id': str(event.event_id), 'outlet_id': event.branch_id,
                        'aggregate_id': event.branch_id, 'timestamp': event.created_at.isoformat()})
                event.published_at = timezone.now()
                event.last_error = ''
            except Exception as exc:
                event.attempts += 1
                event.last_error = str(exc)[:1000]
                event.next_attempt_at = timezone.now()+timedelta(seconds=min(300, 2**min(event.attempts, 8)))
                logger.exception('Daybook event delivery failed: %s', pk)
            event.save()
