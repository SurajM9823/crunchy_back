import uuid
from decimal import Decimal
from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils.text import slugify
from apps.common.models import TimeStampedModel


class Category(TimeStampedModel):
    """
    Catalog category grouping products (e.g. Burgers, Wings, Beverages).
    Maps to Category in frontend (`src/types/index.ts`).
    """
    id = models.CharField(max_length=64, primary_key=True)
    name = models.CharField(max_length=120, db_index=True)
    icon_name = models.CharField(max_length=64, default="Utensils", help_text="Lucide icon identifier")
    display_order = models.PositiveIntegerField(default=0, db_index=True)
    hsn_code = models.CharField(max_length=16, blank=True, default="", help_text="Statutory HSN/SAC code")
    is_archived = models.BooleanField(default=False, db_index=True, help_text="Soft deletion flag")

    class Meta:
        db_table = 'catalog_category'
        verbose_name = 'Category'
        verbose_name_plural = 'Categories'
        ordering = ['display_order', 'name']

    def save(self, *args, **kwargs):
        if not self.id:
            slug = slugify(self.name)
            self.id = f"cat-{slug}" if slug else f"cat-{uuid.uuid4().hex[:8]}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.id})"


class Product(TimeStampedModel):
    """
    Core sellable item.
    Maps to Product in frontend (`src/types/index.ts`).
    """
    COMBO_DISCOUNT_TYPES = (
        ('percentage', 'Percentage'),
        ('fixed_price', 'Fixed Price'),
        ('amount_off', 'Amount Off'),
    )

    id = models.CharField(max_length=64, primary_key=True)
    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name='products',
        db_index=True,
    )
    name = models.CharField(max_length=200, db_index=True)
    description = models.TextField(blank=True, default="")
    base_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.00'))],
        help_text="Base retail price in NPR",
    )
    cost_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="COGS for margin tracking (defaults to ~45% of base price if omitted)",
    )
    prep_time_minutes = models.PositiveIntegerField(
        default=12,
        help_text="Estimated kitchen prep time displayed on product cards & KDS",
    )
    calories = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Caloric energy count (kcal)",
    )
    dietary_tags = models.JSONField(
        default=list,
        blank=True,
        help_text="Array: Halal, Spicy, Vegetarian, Chef's Choice, Popular",
    )
    images = models.JSONField(
        default=list,
        blank=True,
        help_text="Array of image URLs (string[])",
    )
    main_image_index = models.PositiveSmallIntegerField(
        default=0,
        help_text="Index into images array for primary thumbnail",
    )

    # Fulfillment & Channel Visibility Toggles
    is_delivery_eligible = models.BooleanField(default=True, db_index=True)
    is_available = models.BooleanField(default=True, db_index=True, help_text="Master availability toggle")
    is_web_visible = models.BooleanField(default=True, db_index=True)
    show_on_pos = models.BooleanField(default=True, db_index=True)
    show_on_qr = models.BooleanField(default=True, db_index=True)
    discount_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00')), MaxValueValidator(Decimal('100.00'))],
    )

    # Kitchen & Counter Routing Flags (Rule 2 KDS Separation)
    requires_kitchen = models.BooleanField(
        default=True,
        db_index=True,
        help_text="If False, bypasses KDS prep ticket (e.g. canned drinks, cigarettes)",
    )
    is_counter_direct = models.BooleanField(
        default=False,
        help_text="Sold directly across counter",
    )
    is_direct_inventory_item = models.BooleanField(
        default=False,
        help_text="Sold 1:1 directly from stock inventory",
    )

    # Combo Package Configuration
    is_combo_package = models.BooleanField(default=False, db_index=True)
    combo_discount_type = models.CharField(
        max_length=20,
        choices=COMBO_DISCOUNT_TYPES,
        null=True,
        blank=True,
    )
    combo_discount_value = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )
    combo_original_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Calculated sum of individual items before bundle discount",
    )
    combo_items = models.JSONField(
        default=list,
        blank=True,
        help_text="Array of bundled items: [{product_id, quantity, is_default, allow_substitution}]",
    )

    class Meta:
        db_table = 'catalog_product'
        verbose_name = 'Product'
        verbose_name_plural = 'Products'
        ordering = ['category', 'name']

    def save(self, *args, **kwargs):
        if not self.id:
            slug = slugify(self.name)
            self.id = f"prod-{slug}" if slug else f"prod-{uuid.uuid4().hex[:8]}"
        if self.cost_price is None and self.base_price is not None:
            # Default to ~45% COGS as per specification
            self.cost_price = (self.base_price * Decimal('0.45')).quantize(Decimal('0.01'))
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} (NPR {self.base_price})"


class ProductVariant(TimeStampedModel):
    """
    Portion / Size / Patty variants (e.g., Single Patty, Double Patty, Regular, Large).
    Replaces base price when selected.
    """
    id = models.CharField(max_length=64, primary_key=True)
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='variants',
        db_index=True,
    )
    name = models.CharField(max_length=100)
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.00'))],
        help_text="Absolute variant price overriding product base price",
    )
    is_default = models.BooleanField(default=False)

    class Meta:
        db_table = 'catalog_product_variant'
        verbose_name = 'Product Variant'
        verbose_name_plural = 'Product Variants'
        ordering = ['price']

    def save(self, *args, **kwargs):
        if not self.id:
            slug = slugify(self.name)
            self.id = f"var-{slug}-{uuid.uuid4().hex[:4]}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.product.name} - {self.name} (NPR {self.price})"


class ModifierGroup(TimeStampedModel):
    """
    Modifier / Add-on group linked to a product (e.g., "Select Artisan Bun", "Extra Sauces").
    """
    id = models.CharField(max_length=64, primary_key=True)
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='modifier_groups',
        db_index=True,
    )
    name = models.CharField(max_length=120)
    min_selections = models.PositiveSmallIntegerField(default=0)
    max_selections = models.PositiveSmallIntegerField(
        default=1,
        help_text="If 1, acts as radio single-select; if > 1, acts as multi-select checkbox",
    )
    required = models.BooleanField(
        default=False,
        help_text="If True, at least min_selections (>= 1) is required before adding to cart",
    )

    class Meta:
        db_table = 'catalog_modifier_group'
        verbose_name = 'Modifier Group'
        verbose_name_plural = 'Modifier Groups'

    def save(self, *args, **kwargs):
        if not self.id:
            slug = slugify(self.name)
            self.id = f"sec-{slug}-{uuid.uuid4().hex[:4]}"
        if self.required and self.min_selections < 1:
            self.min_selections = 1
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.product.name} -> {self.name}"


class ModifierOption(TimeStampedModel):
    """
    Individual choice within a ModifierGroup (e.g., "Butter Toasted Brioche", "Truffle Mayo").
    """
    id = models.CharField(max_length=64, primary_key=True)
    group = models.ForeignKey(
        ModifierGroup,
        on_delete=models.CASCADE,
        related_name='options',
        db_index=True,
    )
    name = models.CharField(max_length=120)
    price_delta = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text="Additional surcharge in NPR (0.00 for free choice)",
    )
    is_default = models.BooleanField(default=False)

    class Meta:
        db_table = 'catalog_modifier_option'
        verbose_name = 'Modifier Option'
        verbose_name_plural = 'Modifier Options'
        ordering = ['price_delta', 'name']

    def save(self, *args, **kwargs):
        if not self.id:
            slug = slugify(self.name)
            self.id = f"opt-{slug}-{uuid.uuid4().hex[:4]}"
        super().save(*args, **kwargs)

    def __str__(self):
        delta = f"+NPR {self.price_delta}" if self.price_delta > 0 else "Free"
        return f"{self.group.name}: {self.name} ({delta})"


class ProductTimePricing(TimeStampedModel):
    """
    Product-level time slot pricing (e.g. Breakfast price from 07:00 to 11:00).
    """
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='time_pricings',
        db_index=True,
    )
    slot_name = models.CharField(max_length=100)
    start_time = models.TimeField()
    end_time = models.TimeField()
    days = models.CharField(max_length=100, default="All Days", help_text="e.g. 'All Days' or 'Mon-Fri'")
    price = models.DecimalField(max_digits=10, decimal_places=2)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = 'catalog_product_time_pricing'
        verbose_name = 'Product Time Pricing'
        verbose_name_plural = 'Product Time Pricings'


class OutletProductOverride(TimeStampedModel):
    """
    Outlet-scoped availability and price overrides.
    Allows an individual franchise outlet admin to mark items out-of-stock (sold out)
    or adjust local pricing without affecting other branches or the global catalog.
    """
    branch = models.ForeignKey(
        'restaurants.Branch',
        on_delete=models.CASCADE,
        related_name='product_overrides',
        db_index=True,
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='outlet_overrides',
        db_index=True,
    )
    is_available = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Outlet-specific stock toggle (False = Sold Out at this branch)",
    )
    price_override = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Outlet-specific price override (null uses global catalog price)",
    )
    is_web_visible = models.BooleanField(null=True, blank=True)
    show_on_pos = models.BooleanField(null=True, blank=True)
    show_on_qr = models.BooleanField(null=True, blank=True)

    class Meta:
        db_table = 'outlet_product_override'
        verbose_name = 'Outlet Product Override'
        verbose_name_plural = 'Outlet Product Overrides'
        unique_together = ('branch', 'product')

    def __str__(self):
        status_str = "Available" if self.is_available else "OUT OF STOCK"
        return f"{self.branch.name} - {self.product.name}: {status_str}"


class OutletTimePricingSchedule(TimeStampedModel):
    """
    Outlet-wide time-based discount schedules (Happy hour, rush discounts).
    """
    branch = models.ForeignKey(
        'restaurants.Branch',
        on_delete=models.CASCADE,
        related_name='pricing_schedules',
        db_index=True,
    )
    name = models.CharField(max_length=120)
    start_time = models.TimeField()
    end_time = models.TimeField()
    days = models.JSONField(default=list, help_text="List of days: ['Mon', 'Tue', ...]")
    discount_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.00')), MaxValueValidator(Decimal('100.00'))],
    )
    product_ids = models.JSONField(default=list, blank=True, help_text="List of prod-* IDs targeted")
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = 'outlet_time_pricing_schedule'
        verbose_name = 'Outlet Time Pricing Schedule'
        verbose_name_plural = 'Outlet Time Pricing Schedules'

    def __str__(self):
        return f"{self.branch.name}: {self.name} ({self.discount_percentage}%)"

