from django.contrib import admin
from .models import DaybookEntry


@admin.register(DaybookEntry)
class DaybookEntryAdmin(admin.ModelAdmin):
    list_display = ('id', 'branch', 'date', 'direction', 'amount', 'payment_method', 'source', 'voided_at')
    list_filter = ('branch', 'direction', 'source', 'date')
    search_fields = ('party', 'reference', 'description')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
