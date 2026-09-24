from django.contrib import admin
from .models import PaymentTransaction, FiscalInvoice


@admin.register(PaymentTransaction)
class PaymentTransactionAdmin(admin.ModelAdmin):
    list_display = (
        'transaction_id',
        'order',
        'branch',
        'amount',
        'payment_method',
        'status',
        'gateway_ref',
        'created_at',
    )
    list_filter = ('branch', 'payment_method', 'status')
    search_fields = ('transaction_id', 'order__order_number', 'gateway_ref')
    readonly_fields = ('transaction_id', 'created_at')


@admin.register(FiscalInvoice)
class FiscalInvoiceAdmin(admin.ModelAdmin):
    list_display = (
        'invoice_number',
        'order',
        'branch',
        'seller_pan',
        'customer_name',
        'grand_total',
        'payment_method',
        'is_synced_ird',
        'created_at',
    )
    list_filter = ('branch', 'payment_method', 'is_synced_ird')
    search_fields = ('invoice_number', 'order__order_number', 'seller_pan', 'customer_name', 'customer_pan')
    readonly_fields = ('invoice_number', 'created_at')

