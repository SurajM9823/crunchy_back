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


class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 1


class ModifierOptionInline(admin.TabularInline):
    model = ModifierOption
    extra = 2


@admin.register(ModifierGroup)
class ModifierGroupAdmin(admin.ModelAdmin):
    list_display = ('name', 'product', 'min_selections', 'max_selections', 'required')
    search_fields = ('name', 'product__name')
    list_filter = ('required',)
    inlines = [ModifierOptionInline]


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'icon_name', 'display_order', 'hsn_code', 'is_archived', 'created_at')
    search_fields = ('name', 'id')
    list_filter = ('is_archived',)
    ordering = ('display_order', 'name')


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
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
class OutletProductOverrideAdmin(admin.ModelAdmin):
    list_display = ('branch', 'product', 'is_available', 'price_override', 'updated_at')
    list_filter = ('branch', 'is_available')
    search_fields = ('branch__name', 'product__name')


@admin.register(OutletTimePricingSchedule)
class OutletTimePricingScheduleAdmin(admin.ModelAdmin):
    list_display = ('name', 'branch', 'start_time', 'end_time', 'discount_percentage', 'is_active')
    list_filter = ('branch', 'is_active')


@admin.register(ProductTimePricing)
class ProductTimePricingAdmin(admin.ModelAdmin):
    list_display = ('slot_name', 'product', 'start_time', 'end_time', 'price', 'is_active')
    list_filter = ('is_active',)

