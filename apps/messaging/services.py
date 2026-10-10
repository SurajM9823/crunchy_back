from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from .attachments import inspect_upload
from .client_ip import client_ip
from .access import customer_branch, guest_hash
from .models import Conversation, Message, ReadReceipt, ChatEvent


@transaction.atomic
def start_conversation(request):
    branch = customer_branch(request)
    if request.user.is_authenticated:
        thread, _ = Conversation.objects.get_or_create(branch=branch, customer=request.user,
            defaults={'customer_name': (request.user.get_full_name() or request.user.username or 'Customer')[:120]})
    else:
        thread, _ = Conversation.objects.get_or_create(branch=branch, customer=None, guest_hash=guest_hash(request))
    address = client_ip(request)
    if address and thread.last_client_ip != address:
        thread.last_client_ip = address
        thread.save(update_fields=["last_client_ip"])
    return thread


@transaction.atomic
def send_message(thread, user, data, staff=False, address=None):
    data = dict(data)
    upload = data.pop('file', None)
    metadata = inspect_upload(upload) if upload else {}
    thread = Conversation.objects.select_for_update().get(pk=thread.pk)
    if not staff and address and thread.last_client_ip != address:
        thread.last_client_ip = address
        thread.save(update_fields=['last_client_ip'])
    sender_key = f'user:{user.pk}' if user.is_authenticated else f'guest:{thread.guest_hash}'
    old = Message.objects.filter(conversation=thread, sender_key=sender_key, client_id=data['client_id']).first()
    if old:
        if old.text != data['text'] or old.is_staff != staff or old.file_sha256 != metadata.get('file_sha256', '') or old.file_name != metadata.get('file_name', ''): raise ValidationError('This send key was already used for another message.')
        return old
    message = Message(conversation=thread, sender=user if user.is_authenticated else None,
        sender_key=sender_key, is_staff=staff, **data, **metadata)
    try:
        if upload: message.file.save(upload.name, upload, save=False)
        message.save()
        thread.last_message_id = message.pk
        thread.updated_at = timezone.now()
        thread.revision += 1
        thread.save(update_fields=['last_message_id', 'updated_at', 'revision'])
        ChatEvent.objects.create(conversation=thread, message=message)
    except Exception:
        if message.file: message.file.delete(save=False)
        raise
    return message


@transaction.atomic
def mark_read(thread, user, last_id, staff=False):
    thread = Conversation.objects.select_for_update().get(pk=thread.pk)
    last_id = min(last_id, thread.last_message_id)
    if staff:
        receipt, _ = ReadReceipt.objects.get_or_create(conversation=thread, user=user)
        if last_id <= receipt.last_message_id: return
        receipt.last_message_id = last_id
        receipt.save(update_fields=['last_message_id'])
    else:
        if last_id <= thread.customer_read_id: return
        thread.customer_read_id = last_id
    thread.revision += 1
    thread.save(update_fields=['customer_read_id', 'revision'])
    ChatEvent.objects.create(conversation=thread, event_type='CHAT_READ')
