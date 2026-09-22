from django.contrib import admin
from .models import Order, OrderItem, OrderItemModifier, OrderStatusHistory


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ('product', 'product_name', 'variant_name', 'unit_price', 'quantity', 'line_total', 'requires_kitchen', 'round_number')


class OrderStatusHistoryInline(admin.TabularInline):
    model = OrderStatusHistory
    extra = 0
    readonly_fields = ('from_status', 'to_status', 'changed_by', 'notes', 'created_at')


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        'order_number',
        'branch',
        'fulfillment_type',
        'order_source',
        'status',
        'payment_method',
        'payment_status',
        'total_payable',
        'created_at',
    )
    list_filter = ('branch', 'fulfillment_type', 'status', 'payment_method', 'payment_status')
    search_fields = ('order_number', 'customer_name', 'customer_phone', 'branch__name')
    inlines = [OrderItemInline, OrderStatusHistoryInline]

