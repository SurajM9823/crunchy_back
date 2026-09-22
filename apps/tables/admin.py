from django.contrib import admin
from .models import DiningTable


@admin.register(DiningTable)
class DiningTableAdmin(admin.ModelAdmin):
    list_display = ('table_number', 'branch', 'section', 'capacity', 'is_active', 'active_session_id', 'created_at')
    list_filter = ('branch', 'section', 'is_active')
    search_fields = ('table_number', 'branch__name', 'section')

