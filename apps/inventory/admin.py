from django.contrib import admin
from .models import InventoryCategory, InventoryItem, RecipeItem, StockTransaction


@admin.register(InventoryCategory)
class InventoryCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'description')
    search_fields = ('name',)


@admin.register(InventoryItem)
class InventoryItemAdmin(admin.ModelAdmin):
    list_display = ('sku', 'name', 'branch', 'current_stock', 'unit', 'min_threshold', 'cost_per_unit', 'is_active')
    list_filter = ('branch', 'category', 'unit', 'is_active')
    search_fields = ('name', 'sku', 'branch__name')


@admin.register(RecipeItem)
class RecipeItemAdmin(admin.ModelAdmin):
    list_display = ('product', 'variant', 'inventory_item', 'quantity_required')
    list_filter = ('product', 'inventory_item__branch')
    search_fields = ('product__name', 'inventory_item__name')


@admin.register(StockTransaction)
class StockTransactionAdmin(admin.ModelAdmin):
    list_display = ('inventory_item', 'transaction_type', 'quantity', 'previous_stock', 'resulting_stock', 'created_at')
    list_filter = ('transaction_type', 'created_at')
    search_fields = ('inventory_item__name', 'inventory_item__sku', 'notes')
    readonly_fields = ('created_at',)

