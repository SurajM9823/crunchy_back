import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone


class DaybookEntry(models.Model):
    branch = models.ForeignKey('restaurants.Branch', on_delete=models.PROTECT)
    date = models.DateField()
    direction = models.CharField(max_length=3, choices=[('IN', 'Money in'), ('OUT', 'Money out')])
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_method = models.CharField(max_length=32)
    category = models.CharField(max_length=80)
    party = models.CharField(max_length=150, blank=True)
    description = models.CharField(max_length=1000)
    reference = models.CharField(max_length=128, blank=True)
    source = models.CharField(max_length=10, default='MANUAL', choices=[('MANUAL', 'Manual'), ('SALE', 'Sale payment'), ('REFUND', 'Refund'), ('SUPPLIER', 'Supplier payment')])
    payment = models.OneToOneField('payments.PaymentTransaction', null=True, blank=True, on_delete=models.PROTECT)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='recorded_daybook_entries')
    created_at = models.DateTimeField(default=timezone.now)
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='voided_daybook_entries')
    void_reason = models.CharField(max_length=500, blank=True)

    class Meta:
        indexes = [models.Index(fields=['branch', 'date', 'id'], name='daybook_branch_date')]
        constraints = [models.CheckConstraint(condition=models.Q(amount__gt=0), name='daybook_positive_amount')]


class DaybookMutation(models.Model):
    branch = models.ForeignKey('restaurants.Branch', on_delete=models.CASCADE)
    key = models.CharField(max_length=128)
    fingerprint = models.CharField(max_length=64)
    response = models.JSONField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=['branch', 'key'], name='daybook_request_once')]


class DaybookEvent(models.Model):
    event_id = models.UUIDField(default=uuid.uuid4, unique=True)
    branch = models.ForeignKey('restaurants.Branch', on_delete=models.CASCADE)
    event_type = models.CharField(max_length=40)
    created_at = models.DateTimeField(default=timezone.now)
    published_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    last_error = models.CharField(max_length=1000, blank=True)

    class Meta:
        indexes = [models.Index(fields=['published_at', 'next_attempt_at'], name='daybook_events_pending')]
