from decimal import Decimal
from django.db import models
from django.conf import settings
from apps.common.models import TimeStampedModel


class UnitOfMeasure(models.TextChoices):
    PCS = 'PCS', 'Pieces (Count)'
    KG = 'KG', 'Kilograms (kg)'
    GRAMS = 'GRAMS', 'Grams (g)'
    LITERS = 'LITERS', 'Liters (L)'
    MILLILITERS = 'MILLILITERS', 'Milliliters (ml)'
    PACKS = 'PACKS', 'Packs'


class StockTransactionType(models.TextChoices):
    ORDER_DEDUCTION = 'ORDER_DEDUCTION', 'Order Deduction'
    RESTOCK_PURCHASE = 'RESTOCK_PURCHASE', 'Restock / Purchase'
    WASTAGE_SPOILAGE = 'WASTAGE_SPOILAGE', 'Wastage / Spoilage'
    AUDIT_ADJUSTMENT = 'AUDIT_ADJUSTMENT', 'Audit Adjustment'


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
    current_stock = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal('0.000'),
        help_text="Current available quantity in stock",
    )
    unit = models.CharField(
        max_length=32,
        choices=UnitOfMeasure.choices,
        default=UnitOfMeasure.PCS,
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

    def __str__(self):
        return f"{self.name} [{self.sku}] - {self.current_stock} {self.unit} ({self.branch.name})"


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

