import logging
from datetime import timedelta
from asgiref.sync import async_to_sync
from celery import shared_task
from channels.layers import get_channel_layer
from django.db import transaction
from django.utils import timezone
from .models import OrderOutboxEvent

logger=logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60, soft_time_limit=35, time_limit=45)
def generate_pickup_audio(self, text):
    from django.core.cache import cache
    from .announcement_services import generate_audio, audio_identity
    try:
        return generate_audio(text)
    except Exception as exc:
        if self.request.retries >= self.max_retries:
            _, key = audio_identity(text)
            cache.set(key, 'failed', timeout=60)
            logger.exception('Nepali announcement generation failed after retries')
            raise
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


@shared_task(autoretry_for=(Exception,), retry_backoff=60, max_retries=3)
def publish_pos_events():
    ids=list(OrderOutboxEvent.objects.filter(published_at__isnull=True,next_attempt_at__lte=timezone.now()).order_by('created_at').values_list('pk',flat=True)[:100])
    for pk in ids:
        with transaction.atomic():
            event=OrderOutboxEvent.objects.select_for_update().filter(pk=pk).first()
            if event is None: continue
            if event.published_at or event.next_attempt_at>timezone.now(): continue
            try:
                layer=get_channel_layer()
                if layer is None: raise RuntimeError('Channel layer unavailable')
                if event.event_type == 'ORDER_DELETED':
                    publish_deleted_order(layer, event)
                else:
                    async_to_sync(layer.group_send)(f'pos_{event.branch_id}',{'type':'pos_event','event_id':str(event.pk),
                        'event_type':event.event_type,'aggregate_id':event.order_id,'outlet_id':event.branch_id,
                        'timestamp':event.created_at.isoformat(),**event.payload})
                    from apps.customer_web.models import CustomerOrder
                    owner_id = CustomerOrder.objects.filter(order_id=event.order_id).values_list('user_id', flat=True).first()
                    if owner_id:
                        async_to_sync(layer.group_send)(f'customer_orders_{owner_id}', {'type':'customer_event'})
                    # Public pickup displays receive token/status only, never customer/payment data.
                    order=event.order
                    envelope = {'event_id':str(event.pk),'event_type':event.event_type,
                        'aggregate_id':order.pk,'outlet_id':event.branch_id,'timestamp':event.created_at.isoformat(),
                        'order_number':order.order_number,'status':order.status,
                        'fulfillment_type':order.fulfillment_type,
                        'table_number':order.table.table_number if order.table_id else None}
                    envelope['round_number'] = event.payload.get('round_number')
                    envelope['round_status'] = event.payload.get('round_status')
                    # Prepare the spoken token while cooking, in a separate task.
                    # Speech availability must never prevent order event delivery.
                    if order.status in ('ACCEPTED', 'PREPARING', 'READY'):
                        try:
                            from .announcement_services import pickup_token_text, request_audio
                            for number in order.items.filter(is_voided=False).values_list('round_number', flat=True).distinct():
                                request_audio(pickup_token_text(order.order_number, number))
                        except Exception:
                            logger.exception('Could not prepare pickup audio for order %s', order.pk)
                    async_to_sync(layer.group_send)(f'outlet_{event.branch_id}_kitchen',
                        {'type':'kitchen_ticket_update', **envelope})
                    async_to_sync(layer.group_send)(f'order_{order.pk}', {'type':'order_event', **envelope})
                    async_to_sync(layer.group_send)(f'outlet_{event.branch_id}_display',{'type':'display_update',
                        **envelope})
                event.published_at=timezone.now(); event.last_error=''
            except Exception as exc:
                event.attempts+=1; event.last_error=str(exc)[:2000]
                event.next_attempt_at=timezone.now()+timedelta(seconds=min(300,2**min(event.attempts,8)))
                logger.exception('POS outbox delivery failed: %s',pk)
            event.save()


def publish_deleted_order(layer, event):
    order_id = event.payload['aggregate_id']
    envelope = {'event_id': str(event.pk), 'event_type': 'ORDER_DELETED',
                'aggregate_id': order_id, 'outlet_id': event.branch_id,
                'timestamp': event.created_at.isoformat(), 'status': 'DELETED'}
    for group, kind in [(f'pos_{event.branch_id}', 'pos_event'),
                        (f'outlet_{event.branch_id}_kitchen', 'kitchen_ticket_update'),
                        (f'outlet_{event.branch_id}_display', 'display_update'),
                        (f'order_{order_id}', 'order_event')]:
        async_to_sync(layer.group_send)(group, {'type': kind, **envelope})
    if event.payload.get('customer_id'):
        async_to_sync(layer.group_send)(f"customer_orders_{event.payload['customer_id']}",
                                        {'type': 'customer_event'})
