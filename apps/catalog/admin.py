from django.contrib import admin
from .models import (
    Category,
    Product,
    ProductVariant,
    ModifierGroup,
    ModifierOption,
    OutletProductOverride,
    OutletTimePricingSchedule,
    ProductTimePricing,
)


class CatalogReadOnlyAdmin(admin.ModelAdmin):
    """Catalog writes go through the menu API, including nested validation and outbox."""
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser


class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 1


class ModifierOptionInline(admin.TabularInline):
    model = ModifierOption
    extra = 2


@admin.register(ModifierGroup)
class ModifierGroupAdmin(CatalogReadOnlyAdmin):
    list_display = ('name', 'product', 'min_selections', 'max_selections', 'required')
    search_fields = ('name', 'product__name')
    list_filter = ('required',)
    inlines = [ModifierOptionInline]


@admin.register(Category)
class CategoryAdmin(CatalogReadOnlyAdmin):
    list_display = ('id', 'name', 'icon_name', 'display_order', 'hsn_code', 'is_archived', 'created_at')
    search_fields = ('name', 'id')
    list_filter = ('is_archived',)
    ordering = ('display_order', 'name')


@admin.register(Product)
class ProductAdmin(CatalogReadOnlyAdmin):
    list_display = (
        'id',
        'name',
        'category',
        'base_price',
        'is_available',
        'is_combo_package',
        'requires_kitchen',
        'is_delivery_eligible',
        'show_on_pos',
        'show_on_qr',
    )
    search_fields = ('name', 'id', 'description')
    list_filter = (
        'category',
        'is_available',
        'is_combo_package',
        'requires_kitchen',
        'is_delivery_eligible',
        'show_on_pos',
        'show_on_qr',
    )
    inlines = [ProductVariantInline]


@admin.register(OutletProductOverride)
class OutletProductOverrideAdmin(CatalogReadOnlyAdmin):
    list_display = ('branch', 'product', 'is_available', 'price_override', 'updated_at')
    list_filter = ('branch', 'is_available')
    search_fields = ('branch__name', 'product__name')


@admin.register(OutletTimePricingSchedule)
class OutletTimePricingScheduleAdmin(CatalogReadOnlyAdmin):
    list_display = ('name', 'branch', 'start_time', 'end_time', 'discount_percentage', 'is_active')
    list_filter = ('branch', 'is_active')


@admin.register(ProductTimePricing)
class ProductTimePricingAdmin(CatalogReadOnlyAdmin):
    list_display = ('slot_name', 'product', 'start_time', 'end_time', 'price', 'is_active')
    list_filter = ('is_active',)

