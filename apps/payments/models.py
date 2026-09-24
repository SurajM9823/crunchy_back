import uuid
from decimal import Decimal
from django.db import models
from django.conf import settings
from apps.common.models import TimeStampedModel


class TransactionStatus(models.TextChoices):
    PENDING = 'PENDING', 'Pending'
    SUCCESS = 'SUCCESS', 'Success'
    FAILED = 'FAILED', 'Failed'
    REFUNDED = 'REFUNDED', 'Refunded'


class PaymentTransaction(TimeStampedModel):
    """
    Financial transaction log for every payment attempt across all payment gateways and counter cash.
    Includes idempotency keys to prevent double charging at 10k scale.
    """
    transaction_id = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        help_text="Unique transaction reference (e.g. TXN-260923-0001)",
    )
    order = models.ForeignKey(
        'orders.Order',
        on_delete=models.PROTECT,
        related_name='payments',
        db_index=True,
    )
    branch = models.ForeignKey(
        'restaurants.Branch',
        on_delete=models.PROTECT,
        related_name='payments',
        db_index=True,
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_method = models.CharField(max_length=32, db_index=True)
    status = models.CharField(
        max_length=32,
        choices=TransactionStatus.choices,
        default=TransactionStatus.PENDING,
        db_index=True,
    )
    gateway_ref = models.CharField(
        max_length=128,
        blank=True,
        default="",
        help_text="Reference token or transaction ID from payment gateway (Fonepay, eSewa, Khalti)",
    )
    raw_response = models.JSONField(
        default=dict,
        blank=True,
        help_text="Raw payload returned from gateway webhook or terminal",
    )
    idempotency_key = models.CharField(
        max_length=128,
        unique=True,
        null=True,
        blank=True,
        db_index=True,
        help_text="Client idempotency key preventing duplicate payment processing",
    )
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Cashier or staff who collected counter payment",
    )

    class Meta:
        db_table = 'payments_transaction'
        verbose_name = 'Payment Transaction'
        verbose_name_plural = 'Payment Transactions'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.transaction_id} ({self.payment_method}) - NPR {self.amount} [{self.status}]"


class FiscalInvoice(models.Model):
    """
    Official Statutory Tax Invoice (Statutory Fiscal Invoicing Rule).
    Comprises statutory restaurant PAN, 13% tax-inclusive VAT breakdown,
    and statutory cash round-down savings.
    """
    invoice_number = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        help_text="Sequential official tax receipt number (e.g. INV-CB-KTM-000123)",
    )
    order = models.OneToOneField(
        'orders.Order',
        on_delete=models.PROTECT,
        related_name='fiscal_invoice',
        db_index=True,
    )
    branch = models.ForeignKey(
        'restaurants.Branch',
        on_delete=models.PROTECT,
        related_name='fiscal_invoices',
        db_index=True,
    )
    restaurant = models.ForeignKey(
        'restaurants.Restaurant',
        on_delete=models.PROTECT,
        related_name='fiscal_invoices',
        db_index=True,
    )
    fiscal_year = models.CharField(
        max_length=32,
        default="2081/82",
        help_text="Fiscal accounting year",
    )
    seller_pan = models.CharField(
        max_length=32,
        help_text="Statutory PAN Number of restaurant brand",
    )
    customer_name = models.CharField(max_length=120, default="Guest")
    customer_pan = models.CharField(
        max_length=32,
        blank=True,
        default="",
        help_text="Optional customer PAN for corporate / B2B expense invoices",
    )

    # Statutory Financial Breakdown
    subtotal = models.DecimalField(max_digits=12, decimal_places=2)
    taxable_amount = models.DecimalField(max_digits=12, decimal_places=2)
    vat_amount = models.DecimalField(max_digits=12, decimal_places=2, help_text="13% statutory tax portion")
    cash_round_down_savings = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
    )
    grand_total = models.DecimalField(max_digits=12, decimal_places=2)
    payment_method = models.CharField(max_length=32)

    is_synced_ird = models.BooleanField(
        default=False,
        help_text="Flag indicating synchronization with Inland Revenue Department e-billing API",
    )
    printed_count = models.PositiveIntegerField(
        default=1,
        help_text="Number of times physical thermal receipt has been printed",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'payments_fiscal_invoice'
        verbose_name = 'Fiscal Tax Invoice'
        verbose_name_plural = 'Fiscal Tax Invoices'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.invoice_number} - NPR {self.grand_total} (PAN: {self.seller_pan})"

