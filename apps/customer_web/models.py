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


class CustomerAddress(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='delivery_addresses')
    label = models.CharField(max_length=60)
    address = models.CharField(max_length=800)
    landmark = models.CharField(max_length=150, blank=True)
    latitude = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    longitude = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    is_default = models.BooleanField(default=False)

    class Meta:
        ordering = ['-is_default', 'id']
        constraints = [models.UniqueConstraint(fields=['user'], condition=models.Q(is_default=True), name='customer_one_default_address')]


class CustomerCart(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    branch = models.ForeignKey('restaurants.Branch', on_delete=models.CASCADE)
    items = models.JSONField(default=list)
    version = models.PositiveBigIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'branch'], name='customer_cart_scope')]


class CustomerCartMerge(models.Model):
    cart = models.ForeignKey(CustomerCart, on_delete=models.CASCADE)
    key = models.UUIDField()
    fingerprint = models.CharField(max_length=64)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['cart', 'key'], name='customer_cart_merge_once')]


class CustomerOrder(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    order = models.OneToOneField('orders.Order', on_delete=models.PROTECT, related_name='web_customer')
    request_key = models.CharField(max_length=128)
    fingerprint = models.CharField(max_length=64)
    # Receipt evidence stays private; access only through the authenticated endpoint.
    receipt_image = models.BinaryField()
    receipt_type = models.CharField(max_length=32)
    items_payload = models.JSONField(default=list)
    delivery_location = models.JSONField(default=dict, blank=True)
    tip = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    payment_review = models.CharField(max_length=20, default='PENDING')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'request_key'], name='customer_checkout_request_unique')]
