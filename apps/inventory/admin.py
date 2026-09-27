from django.contrib import admin
from .models import (
    InventoryCategory,
    InventoryItem,
    RecipeItem,
    StockTransaction,
    Supplier,
    PurchaseInvoice,
    PurchaseInvoiceItem,
    StockMovementLedger,
    DaybookAccountEntry,
)


@admin.register(InventoryCategory)
class InventoryCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'description')
    search_fields = ('name',)


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'branch', 'phone', 'pan_number', 'credit_balance', 'is_active')
    list_filter = ('branch', 'is_active')
    search_fields = ('name', 'phone', 'pan_number', 'email')


class PurchaseInvoiceItemInline(admin.TabularInline):
    model = PurchaseInvoiceItem
    extra = 0
    fields = ('item', 'item_name', 'category', 'quantity', 'unit', 'unit_cost', 'discount', 'total_cost', 'batch_no', 'expiry_date')


@admin.register(PurchaseInvoice)
class PurchaseInvoiceAdmin(admin.ModelAdmin):
    list_display = (
        'invoice_number',
        'supplier_name',
        'branch',
        'purchase_date',
        'total_amount',
        'paid_amount',
        'due_amount',
        'payment_status',
        'payment_method',
    )
    list_filter = ('branch', 'payment_status', 'payment_method', 'purchase_date')
    search_fields = ('invoice_number', 'supplier_name', 'supplier_phone', 'notes')
    inlines = [PurchaseInvoiceItemInline]
    readonly_fields = ('id', 'created_at', 'updated_at')


@admin.register(PurchaseInvoiceItem)
class PurchaseInvoiceItemAdmin(admin.ModelAdmin):
    list_display = ('item_name', 'purchase', 'quantity', 'unit', 'unit_cost', 'total_cost', 'batch_no', 'expiry_date')
    search_fields = ('item_name', 'purchase__invoice_number', 'batch_no')


@admin.register(StockMovementLedger)
class StockMovementLedgerAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'type',
        'item_name',
        'branch',
        'quantity',
        'unit',
        'previous_stock',
        'new_stock',
        'reason',
        'reference_id',
        'timestamp',
    )
    list_filter = ('branch', 'type', 'reason', 'timestamp')
    search_fields = ('item_name', 'reference_id', 'note')
    readonly_fields = ('timestamp',)


@admin.register(DaybookAccountEntry)
class DaybookAccountEntryAdmin(admin.ModelAdmin):
    list_display = ('id', 'voucher_type', 'party', 'branch', 'dr_amount', 'cr_amount', 'purchase', 'created_at')
    list_filter = ('branch', 'voucher_type', 'created_at')
    search_fields = ('party__name', 'narrative', 'purchase__invoice_number')
    readonly_fields = ('created_at',)


@admin.register(InventoryItem)
class InventoryItemAdmin(admin.ModelAdmin):
    list_display = (
        'sku',
        'name',
        'branch',
        'category',
        'current_stock',
        'unit',
        'min_threshold',
        'cost_per_unit',
        'supplier_name',
        'last_restocked',
        'is_active',
    )
    list_filter = ('branch', 'category', 'unit', 'is_active')
    search_fields = ('name', 'sku', 'branch__name', 'supplier_name')


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
