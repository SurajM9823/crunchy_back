import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone


class CustomerProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='web_profile')
    pin_hash = models.CharField(max_length=128)
    pin_failures = models.PositiveSmallIntegerField(default=0)
    pin_locked_until = models.DateTimeField(null=True, blank=True)
    address = models.CharField(max_length=1000, blank=True)
    favorites = models.ManyToManyField('catalog.Product', blank=True)


class SignupChallenge(models.Model):
    purpose = models.CharField(max_length=12, default='SIGNUP')
    restaurant = models.ForeignKey('restaurants.Restaurant', null=True, on_delete=models.CASCADE)
    created_at = models.DateTimeField(default=timezone.now)

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
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True)
    order = models.OneToOneField('orders.Order', on_delete=models.PROTECT, related_name='web_customer')
    request_key = models.CharField(max_length=128)
    fingerprint = models.CharField(max_length=64)
    # Receipt evidence is never included in public order-tracking responses.
    receipt_image = models.BinaryField()
    receipt_type = models.CharField(max_length=32)
    items_payload = models.JSONField(default=list)
    delivery_location = models.JSONField(default=dict, blank=True)
    tip = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    payment_review = models.CharField(max_length=20, default='PENDING')

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user', 'request_key'], name='customer_checkout_request_unique'),
            models.UniqueConstraint(fields=['request_key'], condition=models.Q(user__isnull=True),
                                    name='customer_guest_checkout_request_unique'),
        ]


class SmsDelivery(models.Model):
    challenge = models.OneToOneField(SignupChallenge, on_delete=models.CASCADE)
    payload_encrypted = models.TextField()
    status = models.CharField(max_length=12, default='PENDING', db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(db_index=True)
    last_error = models.CharField(max_length=100, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class WebsiteVisit(models.Model):
    id = models.UUIDField(primary_key=True, editable=False)
    branch = models.ForeignKey('restaurants.Branch', on_delete=models.CASCADE)
    visitor_hash = models.CharField(max_length=64)
    session_hash = models.CharField(max_length=64)
    path = models.CharField(max_length=100)
    channel = models.CharField(max_length=12)
    device = models.CharField(max_length=10)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [models.Index(fields=['branch', 'created_at'], name='website_visit_branch_date')]


class JourneySession(models.Model):
    """Anonymous browser sessions. Never store contact details or precise locations."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    branch = models.ForeignKey('restaurants.Branch', on_delete=models.CASCADE)
    session_hash = models.CharField(max_length=64)
    visitor_hash = models.CharField(max_length=64)
    first_seen = models.DateTimeField(default=timezone.now)
    last_seen = models.DateTimeField(default=timezone.now)
    landing_path = models.CharField(max_length=200)
    device = models.CharField(max_length=16, default='UNKNOWN')
    browser = models.CharField(max_length=24, default='Unknown')
    os = models.CharField(max_length=24, default='Unknown')
    network = models.CharField(max_length=16, blank=True)
    returning = models.BooleanField(default=False)
    first_touch = models.JSONField(default=dict)
    session_touch = models.JSONField(default=dict)
    last_touch = models.JSONField(default=dict)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['branch', 'session_hash'], name='journey_session_scope')]
        indexes = [models.Index(fields=['branch', '-first_seen'], name='journey_branch_date'),
                   models.Index(fields=['branch', 'visitor_hash'], name='journey_visitor')]


class JourneyEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(JourneySession, on_delete=models.CASCADE, related_name='events')
    name = models.CharField(max_length=48)
    occurred_at = models.DateTimeField(default=timezone.now)
    received_at = models.DateTimeField(default=timezone.now)
    path = models.CharField(max_length=200)
    metadata = models.JSONField(default=dict)
    trusted = models.BooleanField(default=False)
    order = models.ForeignKey('orders.Order', null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        indexes = [models.Index(fields=['session', 'occurred_at'], name='journey_event_time'),
                   models.Index(fields=['name', 'received_at'], name='journey_event_name')]
        constraints = [models.UniqueConstraint(fields=['order', 'name'], condition=models.Q(trusted=True, order__isnull=False), name='journey_order_event_once')]


class PostHogOrderIdentity(models.Model):
    order = models.OneToOneField('orders.Order', on_delete=models.CASCADE)
    visitor_id = models.UUIDField()
    session_id = models.UUIDField()
    created_at = models.DateTimeField(default=timezone.now)


class PostHogDelivery(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey('orders.Order', on_delete=models.CASCADE)
    event = models.CharField(max_length=48)
    payload = models.JSONField()
    project_token = models.CharField(max_length=200)
    api_host = models.URLField()
    status = models.CharField(max_length=12, default='PENDING')
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now, db_index=True)
    created_at = models.DateTimeField(default=timezone.now)
    sent_at = models.DateTimeField(null=True)
    last_error = models.CharField(max_length=80, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['order', 'event'], name='posthog_order_event_once')]


class JourneyReport(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    branch = models.ForeignKey('restaurants.Branch', on_delete=models.CASCADE)
    key = models.CharField(max_length=64, unique=True)
    filters = models.JSONField(default=dict)
    status = models.CharField(max_length=12, default='PENDING')
    result = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    completed_at = models.DateTimeField(null=True)
    attempts = models.PositiveSmallIntegerField(default=0)


class AdMetric(models.Model):
    branch = models.ForeignKey('restaurants.Branch', on_delete=models.CASCADE)
    date = models.DateField()
    source = models.CharField(max_length=40, default='facebook')
    campaign_id = models.CharField(max_length=120, blank=True)
    adset_id = models.CharField(max_length=120, blank=True)
    ad_id = models.CharField(max_length=120)
    impressions = models.PositiveIntegerField(default=0)
    clicks = models.PositiveIntegerField(default=0)
    spend = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['branch', 'date', 'source', 'ad_id'], name='journey_ad_daily')]


class CustomerContact(models.Model):
    branch = models.ForeignKey('restaurants.Branch', on_delete=models.CASCADE)
    phone = models.CharField(max_length=32, blank=True)
    name = models.CharField(max_length=150, blank=True)
    name_key = models.CharField(max_length=450, blank=True, default='')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    sources = models.JSONField(default=list)
    last_seen = models.DateTimeField(default=timezone.now)
    last_login = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['branch', 'phone'], condition=~models.Q(phone=''), name='customer_contact_outlet_phone'),
            models.UniqueConstraint(fields=['branch', 'name_key'], condition=models.Q(phone=''), name='customer_contact_outlet_name'),
        ]
        indexes = [models.Index(fields=['branch', '-last_seen'], name='customer_contact_recent')]


class CustomerCollection(models.Model):
    contact = models.ForeignKey(CustomerContact, on_delete=models.PROTECT, related_name='collections')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    method = models.CharField(max_length=32)
    date = models.DateField()
    reference = models.CharField(max_length=128, blank=True)
    notes = models.CharField(max_length=1000, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    snapshot = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(amount__gt=0), name='customer_collection_positive')]


class CustomerAccountMutation(models.Model):
    branch = models.ForeignKey('restaurants.Branch', on_delete=models.CASCADE)
    key = models.CharField(max_length=128)
    fingerprint = models.CharField(max_length=64)
    response = models.JSONField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=['branch', 'key'], name='customer_account_request_once')]
