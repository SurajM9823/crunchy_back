import uuid
from decimal import Decimal
from django.db import models
from django.core.validators import MinValueValidator
from django.conf import settings
from django.utils import timezone
from apps.common.models import TimeStampedModel


class FulfillmentType(models.TextChoices):
    DINE_IN = 'DINE_IN', 'Dine-In'
    TAKEAWAY = 'TAKEAWAY', 'Takeaway'
    DELIVERY = 'DELIVERY', 'Delivery'
    DRIVE_THRU = 'DRIVE_THRU', 'Drive-Thru'


class OrderSource(models.TextChoices):
    TABLE_QR = 'TABLE_QR', 'Table QR Digital Menu'
    KIOSK = 'KIOSK', 'Self-Service Kiosk'
    POS = 'POS', 'Cashier POS'
    WEBSITE = 'WEBSITE', 'Customer Web Ordering'


class OrderStatus(models.TextChoices):
    PENDING = 'PENDING', 'Pending Confirmation'
    ACCEPTED = 'ACCEPTED', 'Accepted by Kitchen'
    PREPARING = 'PREPARING', 'In Kitchen Preparation'
    READY = 'READY', 'Ready for Serving / Pickup'
    OUT_FOR_DELIVERY = 'OUT_FOR_DELIVERY', 'Out for Delivery'
    COMPLETED = 'COMPLETED', 'Completed & Settled'
    CANCELLED = 'CANCELLED', 'Cancelled'


class PaymentMethod(models.TextChoices):
    CASH = 'CASH', 'Cash'
    CARD = 'CARD', 'Card Payment'
    ESEWA = 'ESEWA', 'eSewa Digital Wallet'
    KHALTI = 'KHALTI', 'Khalti Digital Wallet'
    FONEPAY = 'FONEPAY', 'Fonepay QR'
    PAY_AT_COUNTER = 'PAY_AT_COUNTER', 'Pay at Cashier Counter'


class PaymentStatus(models.TextChoices):
    UNPAID = 'UNPAID', 'Unpaid'
    PAID = 'PAID', 'Paid'
    REFUNDED = 'REFUNDED', 'Refunded'


class Order(TimeStampedModel):
    """
    Centralized Unified Order Entity.
    Acts as the single source of truth across all channels.
    """
    order_number = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        help_text="Short channel token (e.g. W-01, K-01, QR-01, or POS-01)",
    )
    is_pos_managed = models.BooleanField(default=False, db_index=True)
    version = models.PositiveIntegerField(default=1)
    paid_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    credit_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    refunded_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_reason = models.CharField(max_length=255, blank=True, default='')
    billed_at = models.DateTimeField(null=True, blank=True)
    service_charge_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    pricing_policy = models.JSONField(default=dict, blank=True)
    branch = models.ForeignKey(
        'restaurants.Branch',
        on_delete=models.PROTECT,
        related_name='orders',
        db_index=True,
    )
    table = models.ForeignKey(
        'tables.DiningTable',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='orders',
        help_text="Assigned table for DINE_IN orders",
    )
    table_session_id = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Active dining tab session grouping multiple order rounds",
    )
    customer_name = models.CharField(max_length=120, default="Guest")
    customer_phone = models.CharField(max_length=32, blank=True, default="")
    fulfillment_type = models.CharField(
        max_length=32,
        choices=FulfillmentType.choices,
        default=FulfillmentType.DINE_IN,
        db_index=True,
    )
    order_source = models.CharField(
        max_length=32,
        choices=OrderSource.choices,
        default=OrderSource.TABLE_QR,
        db_index=True,
    )
    status = models.CharField(
        max_length=32,
        choices=OrderStatus.choices,
        default=OrderStatus.PENDING,
        db_index=True,
    )
    payment_method = models.CharField(
        max_length=32,
        choices=PaymentMethod.choices,
        default=PaymentMethod.CASH,
    )
    payment_status = models.CharField(
        max_length=32,
        choices=PaymentStatus.choices,
        default=PaymentStatus.UNPAID,
        db_index=True,
    )

    # Statutory Financial Totals (Single Source of Truth)
    subtotal = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
    )
    vat_included_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text="Statutory 13% tax-inclusive VAT portion included in subtotal",
    )
    discount_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
    )
    cash_round_down_savings = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text="Statutory cash currency round-down savings (subtotal - floor(subtotal))",
    )
    total_payable = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
    )

    # Fulfillment Details
    delivery_address = models.TextField(blank=True, default="")
    delivery_lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    delivery_lng = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    notes = models.TextField(blank=True, default="", help_text="Notes and system tags")

    class Meta:
        db_table = 'orders_order'
        verbose_name = 'Order'
        verbose_name_plural = 'Orders'
        ordering = ['-created_at']
        indexes = [models.Index(fields=['branch', '-created_at', '-id'], name='pos_branch_created_idx'),
                   models.Index(fields=['branch', 'status'], name='pos_branch_status_idx')]
        constraints = [models.CheckConstraint(condition=models.Q(paid_amount__gte=0, credit_amount__gte=0, refunded_amount__gte=0), name='pos_amounts_nonnegative'),
                       models.CheckConstraint(condition=models.Q(refunded_amount__lte=models.F('paid_amount')),name='pos_refund_lte_paid')]

    def __str__(self):
        return f"{self.order_number} ({self.get_fulfillment_type_display()}) - NPR {self.total_payable}"


class OrderItem(TimeStampedModel):
    """
    Individual product line item inside an order.
    Maintains historical snapshot of product and variant names/prices.
    """
    combo_components = models.JSONField(default=list, blank=True)
    is_voided = models.BooleanField(default=False)
    void_reason = models.CharField(max_length=255, blank=True, default='')
    stock_consumption = models.JSONField(default=list, blank=True)
    kitchen_status = models.CharField(max_length=16, default='WAITING')
    preparation_started_at = models.DateTimeField(null=True, blank=True)
    ready_at = models.DateTimeField(null=True, blank=True)
    served_at = models.DateTimeField(null=True, blank=True)
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name='items',
        db_index=True,
    )
    product = models.ForeignKey(
        'catalog.Product',
        on_delete=models.PROTECT,
        related_name='order_items',
    )
    product_name = models.CharField(max_length=200, help_text="Historical name snapshot")
    variant = models.ForeignKey(
        'catalog.ProductVariant',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    variant_name = models.CharField(max_length=100, blank=True, default="")
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    quantity = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    line_total = models.DecimalField(max_digits=10, decimal_places=2)
    requires_kitchen = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Rule 2: If False, bypasses KDS preparation ticket (e.g. retail canned drinks)",
    )
    round_number = models.PositiveSmallIntegerField(
        default=1,
        help_text="Table round indicator for active dining sessions",
    )
    item_notes = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        db_table = 'orders_order_item'
        verbose_name = 'Order Item'
        verbose_name_plural = 'Order Items'

    def __str__(self):
        return f"{self.quantity}x {self.product_name} ({self.order.order_number})"


class OrderItemModifier(TimeStampedModel):
    """
    Modifier / extra add-on snapshot selected for a specific OrderItem.
    """
    order_item = models.ForeignKey(
        OrderItem,
        on_delete=models.CASCADE,
        related_name='modifiers',
        db_index=True,
    )
    group_name = models.CharField(max_length=120)
    option_name = models.CharField(max_length=120)
    price_delta = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal('0.00'))

    class Meta:
        db_table = 'orders_item_modifier'
        verbose_name = 'Order Item Modifier'
        verbose_name_plural = 'Order Item Modifiers'

    def __str__(self):
        return f"{self.order_item.product_name}: {self.option_name} (+NPR {self.price_delta})"


class OrderStatusHistory(models.Model):
    """
    Immutable audit timeline tracking all status transitions.
    """
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name='status_history',
        db_index=True,
    )
    from_status = models.CharField(max_length=32)
    to_status = models.CharField(max_length=32)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    notes = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'orders_status_history'
        verbose_name = 'Order Status History'
        verbose_name_plural = 'Order Status Histories'
        ordering = ['created_at']

    def __str__(self):
        return f"{self.order.order_number}: {self.from_status} -> {self.to_status}"


class PosSequence(models.Model):
    branch = models.OneToOneField('restaurants.Branch', on_delete=models.CASCADE, primary_key=True)
    order_counter = models.PositiveBigIntegerField(default=0)
    receipt_counter = models.PositiveBigIntegerField(default=0)


class OrderTokenSequence(models.Model):
    source = models.CharField(max_length=32, choices=OrderSource.choices, primary_key=True)
    counter = models.PositiveBigIntegerField(default=0)


class PosMutation(models.Model):
    branch = models.ForeignKey('restaurants.Branch', on_delete=models.CASCADE)
    key = models.CharField(max_length=128)
    fingerprint = models.CharField(max_length=64)
    response = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['branch', 'key'], name='pos_mutation_key_unique')]


class OrderOutboxEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    branch = models.ForeignKey('restaurants.Branch', on_delete=models.CASCADE)
    order = models.ForeignKey(Order, on_delete=models.CASCADE)
    event_type = models.CharField(max_length=64)
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    last_error = models.TextField(blank=True, default='')

    class Meta:
        indexes = [models.Index(fields=['published_at', 'next_attempt_at'], name='pos_outbox_pending_idx'),
                   models.Index(fields=['branch','-created_at'], name='pos_outbox_branch_idx')]


class PosReceipt(models.Model):
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name='pos_receipts')
    number = models.CharField(max_length=64, unique=True)
    kind = models.CharField(max_length=16, choices=[('TOKEN','Kitchen token'), ('BILL','Bill'), ('REFUND','Refund')])
    snapshot = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)


class PosCreditEntry(models.Model):
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name='credit_entries')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    customer_phone = models.CharField(max_length=32)
    reason = models.CharField(max_length=255)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
