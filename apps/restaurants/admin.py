from django.contrib import admin
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from .models import Restaurant, Branch


class BranchInline(admin.TabularInline):
    """
    Allows Superusers to add Franchise Outlets directly
    while creating or editing a Restaurant Brand.
    """
    model = Branch
    extra = 1
    fields = ('name', 'branch_code', 'city', 'phone_number', 'is_main_branch', 'is_active', 'accepting_orders')
    show_change_link = True


@admin.register(Restaurant)
class RestaurantAdmin(admin.ModelAdmin):
    """
    Superuser Admin interface for managing Restaurant Brands and Franchises.
    """
    list_display = (
        'name',
        'slug',
        'admin_link',
        'branches_badge',
        'pan_number',
        'phone',
        'is_active',
        'created_at',
    )
    list_filter = ('is_active', 'created_at')
    search_fields = ('name', 'slug', 'pan_number', 'admin__username', 'admin__email', 'admin__phone_number')
    prepopulated_fields = {'slug': ('name',)}
    inlines = [BranchInline]
    ordering = ('name',)

    fieldsets = (
        (
            _('Brand Identity'),
            {
                'fields': ('name', 'slug', 'admin', 'logo_url', 'description'),
                'description': _('Select or assign the primary Restaurant Owner/Admin user for this brand.'),
            },
        ),
        (
            _('Statutory & Billing'),
            {
                'fields': ('pan_number',),
                'description': _('Statutory PAN or Tax registration number used for fiscal receipts.'),
            },
        ),
        (
            _('Contact & Web'),
            {
                'fields': ('phone', 'email', 'website'),
            },
        ),
        (
            _('Operational Status'),
            {
                'fields': ('is_active',),
            },
        ),
    )

    @admin.display(description=_('Brand Admin / Owner'))
    def admin_link(self, obj):
        if obj.admin:
            return format_html(
                '<a href="/admin/user_accounts/user/{}/change/"><strong>{}</strong> ({})</a>',
                obj.admin.id,
                obj.admin.username,
                obj.admin.get_role_display(),
            )
        return "-"

    @admin.display(description=_('Outlets Count'))
    def branches_badge(self, obj):
        count = obj.branches.count()
        return format_html(
            '<span style="background: #e0f2fe; color: #0369a1; padding: 3px 8px; border-radius: 12px; font-weight: 600;">{} Outlet(s)</span>',
            count
        )


@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    """
    Superuser and Operations interface for managing Franchise Outlets.
    """
    list_display = (
        'name',
        'branch_code',
        'restaurant_link',
        'manager_display',
        'city',
        'is_main_branch',
        'is_active',
        'accepting_orders',
        'created_at',
    )
    list_filter = ('restaurant', 'city', 'is_main_branch', 'is_active', 'accepting_orders')
    search_fields = ('name', 'branch_code', 'city', 'restaurant__name', 'manager__username', 'manager__email')
    ordering = ('restaurant', 'name')

    fieldsets = (
        (
            _('Branch Identity'),
            {
                'fields': ('restaurant', 'name', 'branch_code', 'manager', 'is_main_branch'),
            },
        ),
        (
            _('Location Details'),
            {
                'fields': ('address_line', 'city', 'state', 'postal_code'),
            },
        ),
        (
            _('Contact Information'),
            {
                'fields': ('phone_number', 'email'),
            },
        ),
        (
            _('Operations Status'),
            {
                'fields': ('is_active', 'accepting_orders'),
            },
        ),
    )

    @admin.display(description=_('Parent Brand'))
    def restaurant_link(self, obj):
        return format_html(
            '<a href="/admin/restaurants/restaurant/{}/change/"><strong>{}</strong></a>',
            obj.restaurant.id,
            obj.restaurant.name
        )

    @admin.display(description=_('Branch Manager'))
    def manager_display(self, obj):
        if obj.manager:
            return obj.manager.username
        return "-"

