from datetime import timedelta
import logging
from asgiref.sync import async_to_sync
from celery import shared_task
from channels.layers import get_channel_layer
from django.db import transaction
from django.utils import timezone
from apps.orders.models import MobilePushDevice
from apps.orders.pos_access import can_access
from apps.orders.push_notifications import send_chat_push, is_unregistered_device_error
from .models import ChatEvent, ChatPushDelivery

logger = logging.getLogger(__name__)


@shared_task
def deliver_chat_events():
    ids = list(ChatEvent.objects.filter(published_at__isnull=True, next_attempt_at__lte=timezone.now()).order_by('created_at').values_list('pk', flat=True)[:100])
    for event_id in ids:
        with transaction.atomic():
            event = ChatEvent.objects.select_for_update(of=('self',)).select_related('conversation__branch', 'message').get(pk=event_id)
            if event.published_at or event.next_attempt_at > timezone.now(): continue
            thread = event.conversation
            # Delivery records are committed independently of WebSocket availability.
            if event.push_dispatched_at is None:
                if event.message and not event.message.is_staff:
                    devices = MobilePushDevice.objects.filter(branch_id=thread.branch_id, active=True).select_related('user', 'branch')
                    ChatPushDelivery.objects.bulk_create([ChatPushDelivery(event=event, device=d, user=d.user) for d in devices if can_access(d.user, d.branch, 'orders')], ignore_conflicts=True)
                event.push_dispatched_at = timezone.now()
            envelope = {'type': 'chat_event', 'event_type': event.event_type, 'event_id': str(event.pk),
                'aggregate_id': str(thread.pk), 'conversation_id': str(thread.pk), 'outlet_id': thread.branch_id,
                'message_id': event.message_id, 'timestamp': event.created_at.isoformat()}
            try:
                layer = get_channel_layer()
                if layer is None: raise RuntimeError('Channel layer unavailable')
                async_to_sync(layer.group_send)(f'chat_staff_{thread.branch_id}', envelope)
                async_to_sync(layer.group_send)(f'chat_thread_{thread.pk}', envelope)
                event.published_at = timezone.now()
            except Exception as error:
                event.attempts += 1
                event.next_attempt_at = timezone.now() + timedelta(seconds=min(300, 2 ** min(event.attempts, 8)))
                logger.warning('Chat event delivery failed %s (%s)', event.pk, type(error).__name__)
            event.save(update_fields=['published_at', 'push_dispatched_at', 'attempts', 'next_attempt_at'])


@shared_task
def deliver_chat_push():
    ids = list(ChatPushDelivery.objects.filter(completed_at__isnull=True, next_attempt_at__lte=timezone.now()).order_by('pk').values_list('pk', flat=True)[:200])
    for pk in ids:
        with transaction.atomic():
            row = ChatPushDelivery.objects.select_for_update(of=('self',)).select_related('event__conversation', 'event__message', 'device__branch__restaurant', 'user').get(pk=pk)
            if row.completed_at or row.next_attempt_at > timezone.now(): continue
            device, event = row.device, row.event
            if row.attempts >= 4 or event.created_at < timezone.now() - timedelta(hours=1):
                row.completed_at = timezone.now(); row.last_error = 'Expired or retry limit reached'
            elif not device.active or device.user_id != row.user_id or device.branch_id != event.conversation.branch_id or not device.branch.is_active or not device.branch.restaurant.is_active or not can_access(row.user, device.branch, 'orders'):
                row.completed_at = timezone.now(); row.last_error = 'Device no longer authorized'
            else:
                try:
                    send_chat_push(device.token, event.pk, event.conversation_id, event.message_id,
                        event.conversation.branch_id, row.user_id, event.conversation.display_name, event.message.text)
                    row.completed_at = timezone.now(); row.last_error = ''
                except Exception as error:
                    row.last_error = type(error).__name__
                    if is_unregistered_device_error(error):
                        MobilePushDevice.objects.filter(pk=device.pk).update(active=False)
                        row.completed_at = timezone.now()
                    elif row.attempts >= 3:
                        row.completed_at = timezone.now()
                    else:
                        row.next_attempt_at = timezone.now() + timedelta(seconds=60 * 2 ** row.attempts)
                    logger.warning('Chat push failed %s (%s)', pk, type(error).__name__)
                row.attempts += 1
            row.save(update_fields=['completed_at', 'last_error', 'attempts', 'next_attempt_at'])
