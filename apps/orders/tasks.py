import logging
from datetime import timedelta
from asgiref.sync import async_to_sync
from celery import shared_task
from channels.layers import get_channel_layer
from django.db import transaction
from django.utils import timezone
from .models import OrderOutboxEvent

logger=logging.getLogger(__name__)


@shared_task(autoretry_for=(Exception,), retry_backoff=60, max_retries=3)
def publish_pos_events():
    ids=list(OrderOutboxEvent.objects.filter(published_at__isnull=True,next_attempt_at__lte=timezone.now()).order_by('created_at').values_list('pk',flat=True)[:100])
    for pk in ids:
        with transaction.atomic():
            event=OrderOutboxEvent.objects.select_for_update().get(pk=pk)
            if event.published_at or event.next_attempt_at>timezone.now(): continue
            try:
                layer=get_channel_layer()
                if layer is None: raise RuntimeError('Channel layer unavailable')
                async_to_sync(layer.group_send)(f'pos_{event.branch_id}',{'type':'pos_event','event_id':str(event.pk),
                    'event_type':event.event_type,'aggregate_id':event.order_id,'outlet_id':event.branch_id,
                    'timestamp':event.created_at.isoformat(),**event.payload})
                # Public pickup displays receive token/status only, never customer/payment data.
                order=event.order
                async_to_sync(layer.group_send)(f'outlet_{event.branch_id}_display',{'type':'display_update',
                    'event_id':str(event.pk),'event_type':event.event_type,'aggregate_id':order.pk,
                    'outlet_id':event.branch_id,'timestamp':event.created_at.isoformat(),
                    'order_number':order.order_number,'status':order.status})
                event.published_at=timezone.now(); event.last_error=''
            except Exception as exc:
                event.attempts+=1; event.last_error=str(exc)[:2000]
                event.next_attempt_at=timezone.now()+timedelta(seconds=min(300,2**min(event.attempts,8)))
                logger.exception('POS outbox delivery failed: %s',pk)
            event.save()
