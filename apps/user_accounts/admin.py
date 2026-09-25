from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.translation import gettext_lazy as _
from .models import User, Employee

# Configure Django Admin Branding for Crunchy RMS
admin.site.site_header = "Crunchy RMS Administration"
admin.site.site_title = "Crunchy RMS Portal"
admin.site.index_title = "Restaurant Operations & Control Center"


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'name',
        'role',
        'title',
        'assigned_outlet',
        'salary_monthly',
        'is_active',
        'joined_date',
        'created_at',
    )
    list_filter = ('role', 'assigned_outlet', 'is_active', 'joined_date')
    search_fields = ('id', 'name', 'email', 'phone', 'title', 'assigned_outlet__name')
    readonly_fields = ('id', 'created_at', 'updated_at')


@admin.register(User)
class CustomUserAdmin(BaseUserAdmin):
    """
    Admin configuration for the custom User model.
    Enables search and filtering across username, email, phone, and restaurant roles.
    """
    list_display = (
        'username',
        'email',
        'phone_number',
        'role',
        'restaurant',
        'branch',
        'is_staff',
        'is_superuser',
        'is_verified',
        'is_active',
        'created_at',
    )
    list_filter = ('role', 'restaurant', 'branch', 'is_staff', 'is_superuser', 'is_active', 'is_verified')
    search_fields = ('username', 'email', 'phone_number', 'first_name', 'last_name')
    ordering = ('-created_at',)

    fieldsets = (
        (None, {'fields': ('username', 'password')}),
        (_('Personal info'), {'fields': ('first_name', 'last_name', 'email', 'phone_number')}),
        (_('Franchise & Outlet Assignment'), {'fields': ('role', 'restaurant', 'branch', 'is_verified')}),
        (
            _('Permissions'),
            {
                'fields': (
                    'is_active',
                    'is_staff',
                    'is_superuser',
                    'groups',
                    'user_permissions',
                ),
            },
        ),
        (_('Important dates'), {'fields': ('last_login', 'date_joined')}),
    )

    add_fieldsets = (
        (
            None,
            {
                'classes': ('wide',),
                'fields': (
                    'username',
                    'email',
                    'phone_number',
                    'role',
                    'password1',
                    'password2',
                    'is_staff',
                    'is_superuser',
                ),
            },
        ),
    )

