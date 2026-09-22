from django import forms
from django.contrib import admin
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from apps.user_accounts.models import User, UserRole
from apps.user_accounts.services import user_create
from .models import Restaurant, Branch, OperateType


class BranchInline(admin.TabularInline):
    """
    Allows Superusers to add Franchise Outlets directly
    while creating or editing a Restaurant Brand.
    """
    model = Branch
    extra = 1
    fields = (
        'name',
        'branch_code',
        'operate_type',
        'manager',
        'city',
        'phone_number',
        'is_main_branch',
        'is_active',
        'accepting_orders',
    )
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


class BranchAdminForm(forms.ModelForm):
    """
    Custom Branch admin form providing a 'Quick Create Outlet Admin'
    feature directly on the Branch editor.
    """
    new_admin_username = forms.CharField(
        required=False,
        label=_('New Admin Username'),
        help_text=_('Enter username to automatically create a new Outlet Administrator.'),
    )
    new_admin_phone = forms.CharField(
        required=False,
        label=_('New Admin Phone'),
        help_text=_('Contact number for the new admin (e.g. +977 9841234567).'),
    )
    new_admin_email = forms.EmailField(
        required=False,
        label=_('New Admin Email'),
        help_text=_('Email address for the new admin.'),
    )
    new_admin_password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False),
        label=_('New Admin Password'),
        help_text=_('Set password for the new Outlet Administrator.'),
    )

    class Meta:
        model = Branch
        fields = '__all__'

    def clean(self):
        cleaned_data = super().clean()
        username = cleaned_data.get('new_admin_username')
        phone = cleaned_data.get('new_admin_phone')
        email = cleaned_data.get('new_admin_email')
        password = cleaned_data.get('new_admin_password')

        has_any_new_admin_field = bool(username or phone or email or password)

        if has_any_new_admin_field:
            if not password:
                self.add_error('new_admin_password', _('Password is required when creating a new Outlet Admin.'))
            if not (username or phone or email):
                self.add_error('new_admin_username', _('Provide at least one identifier (username, phone, or email).'))

        return cleaned_data


@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    """
    Superuser interface for managing Franchise Outlets,
    assigning operate types, and provisioning outlet admins.
    """
    form = BranchAdminForm
    list_display = (
        'name',
        'branch_code',
        'restaurant_link',
        'operate_type_badge',
        'manager_display',
        'channels_summary',
        'city',
        'is_main_branch',
        'is_active',
        'accepting_orders',
    )
    list_filter = (
        'operate_type',
        'restaurant',
        'city',
        'is_main_branch',
        'is_active',
        'accepting_orders',
        'enable_dine_in',
        'enable_delivery',
        'enable_takeaway',
    )
    search_fields = (
        'name',
        'branch_code',
        'city',
        'restaurant__name',
        'manager__username',
        'manager__email',
        'manager__phone_number',
    )
    ordering = ('restaurant', 'name')

    fieldsets = (
        (
            _('Branch Identity'),
            {
                'fields': ('restaurant', 'name', 'branch_code', 'is_main_branch'),
            },
        ),
        (
            _('Operational Model & Operate Type'),
            {
                'fields': ('operate_type',),
                'description': _('Select whether this outlet operates as full Dine-In, Cloud Kitchen, Express Takeout, Drive-Thru, etc.'),
            },
        ),
        (
            _('Fulfillment Channel Capabilities'),
            {
                'fields': (
                    'enable_dine_in',
                    'enable_takeaway',
                    'enable_delivery',
                    'enable_drive_thru',
                    'enable_qr_ordering',
                    'enable_kiosk',
                    'enable_pos',
                ),
                'classes': ('collapse',),
                'description': _('Fine-grained control over which ordering and fulfillment channels are active for this branch.'),
            },
        ),
        (
            _('Assigned Outlet Administrator'),
            {
                'fields': ('manager',),
                'description': _('Select an existing manager/staff member as the Outlet Admin.'),
            },
        ),
        (
            _('⚡ Quick Create New Outlet Admin (Optional)'),
            {
                'fields': (
                    'new_admin_username',
                    'new_admin_phone',
                    'new_admin_email',
                    'new_admin_password',
                ),
                'description': _('Quickly provision a brand-new administrator for this branch without leaving this screen.'),
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

    def save_model(self, request, obj, form, change):
        cleaned_data = form.cleaned_data
        new_username = cleaned_data.get('new_admin_username')
        new_phone = cleaned_data.get('new_admin_phone')
        new_email = cleaned_data.get('new_admin_email')
        new_password = cleaned_data.get('new_admin_password')

        # Auto-create new Outlet Admin user if provided
        if new_password and (new_username or new_phone or new_email):
            new_user = user_create(
                username=new_username,
                phone_number=new_phone,
                email=new_email,
                password=new_password,
                role=UserRole.BRANCH_MANAGER,
                is_staff=True,
                is_verified=True,
            )
            obj.manager = new_user

        super().save_model(request, obj, form, change)

        # Update manager with branch and restaurant references
        if obj.manager:
            obj.manager.branch = obj
            obj.manager.restaurant = obj.restaurant
            if obj.manager.role in (UserRole.CUSTOMER, UserRole.WAITER, UserRole.CASHIER):
                obj.manager.role = UserRole.BRANCH_MANAGER
            obj.manager.is_staff = True
            obj.manager.save(update_fields=['branch', 'restaurant', 'role', 'is_staff'])

    @admin.display(description=_('Parent Brand'))
    def restaurant_link(self, obj):
        return format_html(
            '<a href="/admin/restaurants/restaurant/{}/change/"><strong>{}</strong></a>',
            obj.restaurant.id,
            obj.restaurant.name
        )

    @admin.display(description=_('Operate Type'))
    def operate_type_badge(self, obj):
        colors = {
            OperateType.DINE_IN: ('#dbeafe', '#1e40af'),
            OperateType.EXPRESS_TAKEOUT: ('#fef3c7', '#92400e'),
            OperateType.CLOUD_KITCHEN: ('#f3e8ff', '#6b21a8'),
            OperateType.DRIVE_THRU: ('#fee2e2', '#991b1b'),
            OperateType.FOOD_TRUCK_KIOSK: ('#e0e7ff', '#3730a3'),
            OperateType.HYBRID: ('#dcfce7', '#166534'),
        }
        bg, text = colors.get(obj.operate_type, ('#f1f5f9', '#334155'))
        return format_html(
            '<span style="background: {}; color: {}; padding: 3px 8px; border-radius: 6px; font-weight: 600; font-size: 11px;">{}</span>',
            bg, text, obj.get_operate_type_display()
        )

    @admin.display(description=_('Outlet Admin'))
    def manager_display(self, obj):
        if obj.manager:
            return format_html(
                '<a href="/admin/user_accounts/user/{}/change/"><strong>{}</strong></a>',
                obj.manager.id,
                obj.manager.username or obj.manager.email or obj.manager.phone_number
            )
        return format_html('<span style="color: #94a3b8; font-style: italic;">Unassigned</span>')

    @admin.display(description=_('Channels Active'))
    def channels_summary(self, obj):
        active = []
        if obj.enable_dine_in:
            active.append('Dine-in')
        if obj.enable_takeaway:
            active.append('Takeaway')
        if obj.enable_delivery:
            active.append('Delivery')
        if obj.enable_qr_ordering:
            active.append('QR')
        return ", ".join(active) if active else "None"
