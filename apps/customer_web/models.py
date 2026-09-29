import uuid
from django.conf import settings
from django.db import models


class CustomerProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='web_profile')
    pin_hash = models.CharField(max_length=128)
    pin_failures = models.PositiveSmallIntegerField(default=0)
    pin_locked_until = models.DateTimeField(null=True, blank=True)
    address = models.CharField(max_length=1000, blank=True)
    favorites = models.ManyToManyField('catalog.Product', blank=True)


class SignupChallenge(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    phone = models.CharField(max_length=20, db_index=True)
    code_hash = models.CharField(max_length=128)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    verified = models.BooleanField(default=False)
    consumed = models.BooleanField(default=False)


class CustomerOrder(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    order = models.OneToOneField('orders.Order', on_delete=models.PROTECT, related_name='web_customer')
    request_key = models.CharField(max_length=128)
    fingerprint = models.CharField(max_length=64)
    # Receipt evidence stays private; access only through the authenticated endpoint.
    receipt_image = models.BinaryField()
    receipt_type = models.CharField(max_length=32)
    items_payload = models.JSONField(default=list)
    tip = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    payment_review = models.CharField(max_length=20, default='PENDING')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'request_key'], name='customer_checkout_request_unique')]
