from django.contrib import admin
from .models import DeliveryZone, RiderProfile, DeliveryDispatch


@admin.register(DeliveryZone)
class DeliveryZoneAdmin(admin.ModelAdmin):
    list_display = ('name', 'branch', 'radius_km', 'delivery_fee', 'min_order_amount', 'estimated_delivery_minutes', 'is_active')
    list_filter = ('branch', 'is_active')
    search_fields = ('name', 'branch__name')


@admin.register(RiderProfile)
class RiderProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'branch', 'vehicle_type', 'vehicle_plate', 'status', 'total_deliveries', 'is_active')
    list_filter = ('branch', 'vehicle_type', 'status', 'is_active')
    search_fields = ('user__username', 'vehicle_plate', 'branch__name')


@admin.register(DeliveryDispatch)
class DeliveryDispatchAdmin(admin.ModelAdmin):
    list_display = ('dispatch_id', 'order', 'branch', 'rider', 'status', 'created_at', 'delivered_at')
    list_filter = ('branch', 'status')
    search_fields = ('dispatch_id', 'order__order_number', 'rider__user__username')
    readonly_fields = ('dispatch_id', 'created_at')

