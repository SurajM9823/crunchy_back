from django.contrib import admin
from .models import SmsDelivery


@admin.register(SmsDelivery)
class SmsDeliveryAdmin(admin.ModelAdmin):
    list_display = ('id', 'challenge_id', 'status', 'attempts', 'last_error', 'updated_at')
    list_filter = ('status',)
    fields = ('challenge_id', 'status', 'attempts', 'last_error', 'next_attempt_at', 'updated_at')
    readonly_fields = fields

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
