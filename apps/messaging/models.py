import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone


class Conversation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    branch = models.ForeignKey('restaurants.Branch', on_delete=models.PROTECT)
    customer = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT)
    last_client_ip = models.GenericIPAddressField(null=True, blank=True)
    guest_hash = models.CharField(max_length=64, blank=True)
    customer_name = models.CharField(max_length=120, default='Guest')
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)
    last_message_id = models.PositiveBigIntegerField(default=0, db_index=True)
    customer_read_id = models.PositiveBigIntegerField(default=0)
    revision = models.PositiveBigIntegerField(default=0)

    @property
    def display_name(self):
        return self.customer_name if self.customer_id else f'Guest {str(self.pk)[:8]}'

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['branch', 'customer'], condition=models.Q(customer__isnull=False), name='chat_customer_outlet'),
            models.UniqueConstraint(fields=['branch', 'guest_hash'], condition=models.Q(customer__isnull=True), name='chat_guest_outlet'),
        ]
        indexes = [models.Index(fields=['branch', '-last_message_id'], name='chat_inbox_idx')]


class Message(models.Model):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='messages')
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT)
    sender_key = models.CharField(max_length=80)
    is_staff = models.BooleanField(default=False)
    client_id = models.UUIDField()
    text = models.CharField(max_length=2000)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['conversation', 'sender_key', 'client_id'], name='chat_message_once')]
        indexes = [models.Index(fields=['conversation', 'id'], name='chat_history_idx')]


class ReadReceipt(models.Model):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    last_message_id = models.PositiveBigIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['conversation', 'user'], name='chat_reader_once')]


class ChatEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE)
    message = models.OneToOneField(Message, null=True, on_delete=models.CASCADE)
    event_type = models.CharField(max_length=24, default='CHAT_MESSAGE')
    created_at = models.DateTimeField(default=timezone.now)
    published_at = models.DateTimeField(null=True)
    push_dispatched_at = models.DateTimeField(null=True)
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now, db_index=True)


class ChatPushDelivery(models.Model):
    event = models.ForeignKey(ChatEvent, on_delete=models.CASCADE)
    device = models.ForeignKey('orders.MobilePushDevice', on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now, db_index=True)
    completed_at = models.DateTimeField(null=True)
    last_error = models.CharField(max_length=150, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['event', 'device'], name='chat_push_once')]
