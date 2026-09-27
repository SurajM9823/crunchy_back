import uuid
from decimal import Decimal
from django.db import models
from django.conf import settings
from django.utils import timezone
from apps.common.models import TimeStampedModel


class UnitOfMeasure(models.TextChoices):
    PCS = 'PCS', 'Pieces (Count)'
    KG = 'KG', 'Kilograms (kg)'
    GRAMS = 'GRAMS', 'Grams (g)'
    LITERS = 'LITERS', 'Liters (L)'
    MILLILITERS = 'MILLILITERS', 'Milliliters (ml)'
    PACKS = 'PACKS', 'Packs'
    BOXES = 'BOXES', 'Boxes'
    BOTTLES = 'BOTTLES', 'Bottles'
    CANS = 'CANS', 'Cans'


class StockTransactionType(models.TextChoices):
    ORDER_DEDUCTION = 'ORDER_DEDUCTION', 'Order Deduction'
    RESTOCK_PURCHASE = 'RESTOCK_PURCHASE', 'Restock / Purchase'
    WASTAGE_SPOILAGE = 'WASTAGE_SPOILAGE', 'Wastage / Spoilage'
    AUDIT_ADJUSTMENT = 'AUDIT_ADJUSTMENT', 'Audit Adjustment'


class PaymentStatus(models.TextChoices):
    PAID = 'PAID', 'Paid'
    PARTIAL = 'PARTIAL', 'Partial'
    PENDING = 'PENDING', 'Pending'


class PaymentMethod(models.TextChoices):
    CASH = 'CASH', 'Cash'
    FONEPAY = 'FONEPAY', 'FonePay QR'
    BANK_TRANSFER = 'BANK_TRANSFER', 'Bank Transfer'
    CHEQUE = 'CHEQUE', 'Cheque'
    CREDIT = 'CREDIT', 'Credit (Party Khata)'


class StockMovementType(models.TextChoices):
    INCREASE = 'INCREASE', 'Increase'
    DECREASE = 'DECREASE', 'Decrease'


class InventoryCategory(TimeStampedModel):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, default="")

    class Meta:
        db_table = 'inventory_category'
        verbose_name = 'Inventory Category'
        verbose_name_plural = 'Inventory Categories'
        ordering = ['name']

    def __str__(self):
        return self.name


class Supplier(TimeStampedModel):
    id = models.CharField(max_length=64, primary_key=True)
    branch = models.ForeignKey(
        'restaurants.Branch',
        on_delete=models.CASCADE,
        related_name='suppliers',
        db_index=True,
    )
    name = models.CharField(max_length=150, db_index=True)
    pan_number = models.CharField(max_length=50, blank=True, default='')
    phone = models.CharField(max_length=50, blank=True, default='')
    email = models.EmailField(blank=True, default='')
    address = models.TextField(blank=True, default='')
    credit_balance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text="Current accounts payable / credit khata balance in NPR"
    )
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = 'inventory_supplier'
        verbose_name = 'Supplier'
        verbose_name_plural = 'Suppliers'
        ordering = ['name']
        unique_together = ('branch', 'name')

    def save(self, *args, **kwargs):
        if not self.id:
            self.id = f"sup-{uuid.uuid4().hex[:8]}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.branch.name})"


class InventoryItem(TimeStampedModel):
    """
    Branch-specific stock item (Raw ingredient or direct retail item).
    Supports high-concurrency atomic stock deduction.
    """
    branch = models.ForeignKey(
        'restaurants.Branch',
        on_delete=models.CASCADE,
        related_name='inventory_items',
        db_index=True,
    )
    sku = models.CharField(
        max_length=64,
        db_index=True,
        help_text="Stock Keeping Unit (e.g. SKU-BUN-BRIOCHE, SKU-CAN-COKE-330)",
    )
    name = models.CharField(
        max_length=150,
        db_index=True,
        help_text="Item name (e.g. Brioche Bun, Beef Patty 150g, Canned Cola)",
    )
    category = models.ForeignKey(
        InventoryCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='items',
    )
    supplier = models.ForeignKey(
        Supplier,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='inventory_items',
    )
    supplier_name = models.CharField(max_length=150, blank=True, default='')
    current_stock = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal('0.000'),
        help_text="Current available quantity in stock",
    )
    unit = models.CharField(
        max_length=32,
        default='PCS',
    )
    min_threshold = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal('5.000'),
        help_text="Threshold below which low-stock alert triggers",
    )
    cost_per_unit = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text="Purchase unit cost in NPR",
    )
    last_restocked = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = 'inventory_item'
        verbose_name = 'Inventory Item'
        verbose_name_plural = 'Inventory Items'
        unique_together = ('branch', 'sku')
        ordering = ['branch', 'name']

    @property
    def is_low_stock(self) -> bool:
        return self.current_stock <= self.min_threshold

    @property
    def total_valuation(self) -> Decimal:
        return (self.current_stock * self.cost_per_unit).quantize(Decimal('0.01'))

    def __str__(self):
        return f"{self.name} [{self.sku}] - {self.current_stock} {self.unit} ({self.branch.name})"


class PurchaseInvoice(TimeStampedModel):
    """
    Inward procurement bill received from suppliers.
    """
    id = models.CharField(max_length=64, primary_key=True)
    branch = models.ForeignKey(
        'restaurants.Branch',
        on_delete=models.CASCADE,
        related_name='purchase_invoices',
        db_index=True,
    )
    invoice_number = models.CharField(max_length=100, db_index=True)
    supplier = models.ForeignKey(
        Supplier,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='purchases',
    )
    supplier_name = models.CharField(max_length=150)
    supplier_phone = models.CharField(max_length=50, blank=True, default='')
    purchase_date = models.DateField(default=timezone.now)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    paid_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    due_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    payment_status = models.CharField(
        max_length=20,
        choices=PaymentStatus.choices,
        default=PaymentStatus.PAID,
    )
    payment_method = models.CharField(
        max_length=30,
        choices=PaymentMethod.choices,
        default=PaymentMethod.CASH,
    )
    notes = models.TextField(blank=True, default='')
    document = models.FileField(upload_to='inventory/invoices/', null=True, blank=True)
    document_name = models.CharField(max_length=255, blank=True, default='')
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='received_purchases',
    )
    idempotency_key = models.CharField(max_length=128, blank=True, null=True, unique=True, db_index=True)

    class Meta:
        db_table = 'inventory_purchase_invoice'
        verbose_name = 'Purchase Invoice'
        verbose_name_plural = 'Purchase Invoices'
        ordering = ['-purchase_date', '-created_at']

    def save(self, *args, **kwargs):
        if not self.id:
            self.id = f"pur-{uuid.uuid4().hex[:8]}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.invoice_number} - {self.supplier_name} ({self.total_amount} NPR)"


class PurchaseInvoiceItem(TimeStampedModel):
    """
    Line item inside an inward purchase invoice.
    """
    id = models.CharField(max_length=64, primary_key=True)
    purchase = models.ForeignKey(
        PurchaseInvoice,
        on_delete=models.CASCADE,
        related_name='items',
    )
    item = models.ForeignKey(
        InventoryItem,
        on_delete=models.CASCADE,
        related_name='purchase_items',
    )
    item_name = models.CharField(max_length=150)
    category = models.CharField(max_length=100, blank=True, default='')
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    unit = models.CharField(max_length=32, default='pcs')
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2)
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    total_cost = models.DecimalField(max_digits=12, decimal_places=2)
    batch_no = models.CharField(max_length=100, blank=True, default='')
    expiry_date = models.DateField(null=True, blank=True)

    class Meta:
        db_table = 'inventory_purchase_invoice_item'
        verbose_name = 'Purchase Invoice Item'
        verbose_name_plural = 'Purchase Invoice Items'

    def save(self, *args, **kwargs):
        if not self.id:
            self.id = f"pii-{uuid.uuid4().hex[:8]}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.item_name} x {self.quantity} ({self.total_cost} NPR)"


class StockMovementLedger(models.Model):
    """
    Comprehensive stock ledger for inward purchases, sale deductions, and physical audits.
    """
    id = models.CharField(max_length=64, primary_key=True)
    branch = models.ForeignKey(
        'restaurants.Branch',
        on_delete=models.CASCADE,
        related_name='stock_movements',
        db_index=True,
    )
    item = models.ForeignKey(
        InventoryItem,
        on_delete=models.CASCADE,
        related_name='movements',
    )
    item_name = models.CharField(max_length=150)
    category = models.CharField(max_length=100, blank=True, default='')
    type = models.CharField(max_length=20, choices=StockMovementType.choices, db_index=True)
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    unit = models.CharField(max_length=32, default='pcs')
    previous_stock = models.DecimalField(max_digits=12, decimal_places=3)
    new_stock = models.DecimalField(max_digits=12, decimal_places=3)
    reason = models.CharField(max_length=50, db_index=True)
    reference_id = models.CharField(max_length=100, blank=True, default='')
    note = models.TextField(blank=True, default='')
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'inventory_stock_movement_ledger'
        verbose_name = 'Stock Movement Ledger'
        verbose_name_plural = 'Stock Movement Ledgers'
        ordering = ['-timestamp']

    def save(self, *args, **kwargs):
        if not self.id:
            self.id = f"mov-{uuid.uuid4().hex[:8]}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.type} {self.quantity} {self.unit} - {self.item_name} ({self.reason})"


class DaybookAccountEntry(models.Model):
    """
    Accounting entry booking accounts payable for credit purchases (Party Khata).
    """
    id = models.CharField(max_length=64, primary_key=True)
    branch = models.ForeignKey(
        'restaurants.Branch',
        on_delete=models.CASCADE,
        related_name='daybook_entries',
        db_index=True,
    )
    party = models.ForeignKey(
        Supplier,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='daybook_entries',
    )
    purchase = models.ForeignKey(
        PurchaseInvoice,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='daybook_entries',
    )
    dr_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    cr_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    voucher_type = models.CharField(max_length=50, default='PURCHASE')
    narrative = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'inventory_daybook_account_entry'
        verbose_name = 'Daybook Account Entry'
        verbose_name_plural = 'Daybook Account Entries'
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if not self.id:
            self.id = f"dbk-{uuid.uuid4().hex[:8]}"
        super().save(*args, **kwargs)

    def __str__(self):
        party_name = self.party.name if self.party else 'General'
        return f"{self.voucher_type} - {party_name} Cr:{self.cr_amount} Dr:{self.dr_amount}"


class RecipeItem(TimeStampedModel):
    """
    Bill of Materials (BOM) linking a menu Product/Variant to its required raw ingredients.
    When a customer orders the item, these raw ingredients are atomically deducted.
    """
    product = models.ForeignKey(
        'catalog.Product',
        on_delete=models.CASCADE,
        related_name='recipe_items',
        db_index=True,
    )
    variant = models.ForeignKey(
        'catalog.ProductVariant',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='recipe_items',
        help_text="If set, ingredient applies specifically to this variant (e.g. Double Patty)",
    )
    inventory_item = models.ForeignKey(
        InventoryItem,
        on_delete=models.CASCADE,
        related_name='used_in_recipes',
        db_index=True,
    )
    quantity_required = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        default=Decimal('1.000'),
        help_text="Quantity of raw inventory item consumed per menu portion",
    )

    class Meta:
        db_table = 'inventory_recipe_item'
        verbose_name = 'Recipe Ingredient'
        verbose_name_plural = 'Recipe Ingredients'

    def __str__(self):
        target = f"{self.product.name} ({self.variant.name})" if self.variant else self.product.name
        return f"{target} -> {self.quantity_required} {self.inventory_item.unit} of {self.inventory_item.name}"


class StockTransaction(models.Model):
    """
    Audit ledger tracking every stock addition, order consumption, or wastage.
    """
    inventory_item = models.ForeignKey(
        InventoryItem,
        on_delete=models.CASCADE,
        related_name='transactions',
        db_index=True,
    )
    transaction_type = models.CharField(
        max_length=32,
        choices=StockTransactionType.choices,
        db_index=True,
    )
    quantity = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        help_text="Positive for additions/restock, negative for order consumption/wastage",
    )
    previous_stock = models.DecimalField(max_digits=12, decimal_places=3)
    resulting_stock = models.DecimalField(max_digits=12, decimal_places=3)
    reference_order = models.ForeignKey(
        'orders.Order',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='stock_deductions',
    )
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    notes = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'inventory_transaction'
        verbose_name = 'Stock Transaction'
        verbose_name_plural = 'Stock Transactions'
        ordering = ['-created_at', '-id']

    def __str__(self):
        return f"{self.inventory_item.sku}: {self.quantity} ({self.transaction_type})"
