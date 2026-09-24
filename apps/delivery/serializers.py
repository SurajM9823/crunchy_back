from rest_framework import serializers
from .models import DeliveryZone, RiderProfile, DeliveryDispatch
from .geo_tracker import get_rider_live_location, haversine_distance_km


class DeliveryZoneSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeliveryZone
        fields = [
            'id',
            'branch',
            'name',
            'radius_km',
            'center_lat',
            'center_lng',
            'delivery_fee',
            'min_order_amount',
            'free_delivery_threshold',
            'estimated_delivery_minutes',
            'is_active',
        ]


class DeliveryZoneCreateUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeliveryZone
        fields = [
            'name',
            'radius_km',
            'center_lat',
            'center_lng',
            'delivery_fee',
            'min_order_amount',
            'free_delivery_threshold',
            'estimated_delivery_minutes',
            'is_active',
        ]


class RiderProfileSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)
    full_name = serializers.CharField(source='user.get_full_name', read_only=True)
    phone_number = serializers.CharField(source='user.phone_number', read_only=True)
    live_location = serializers.SerializerMethodField()

    class Meta:
        model = RiderProfile
        fields = [
            'id',
            'user',
            'username',
            'full_name',
            'phone_number',
            'branch',
            'vehicle_type',
            'vehicle_plate',
            'status',
            'total_deliveries',
            'is_active',
            'live_location',
        ]

    def get_live_location(self, obj):
        return get_rider_live_location(obj.id)


class DeliveryDispatchSerializer(serializers.ModelSerializer):
    order_number = serializers.CharField(source='order.order_number', read_only=True)
    total_payable = serializers.DecimalField(source='order.total_payable', max_digits=12, decimal_places=2, read_only=True)
    customer_name = serializers.CharField(source='order.customer_name', read_only=True)
    customer_phone = serializers.CharField(source='order.customer_phone', read_only=True)
    rider_name = serializers.CharField(source='rider.user.username', read_only=True)
    rider_phone = serializers.CharField(source='rider.user.phone_number', read_only=True)
    rider_plate = serializers.CharField(source='rider.vehicle_plate', read_only=True)
    zone_name = serializers.CharField(source='zone.name', read_only=True)
    live_tracking = serializers.SerializerMethodField()

    class Meta:
        model = DeliveryDispatch
        fields = [
            'id',
            'dispatch_id',
            'order',
            'order_number',
            'total_payable',
            'customer_name',
            'customer_phone',
            'branch',
            'rider',
            'rider_name',
            'rider_phone',
            'rider_plate',
            'zone',
            'zone_name',
            'status',
            'delivery_address',
            'destination_lat',
            'destination_lng',
            'rider_notes',
            'assigned_at',
            'picked_up_at',
            'delivered_at',
            'created_at',
            'live_tracking',
        ]

    def get_live_tracking(self, obj):
        if not obj.rider_id:
            return None
        loc = get_rider_live_location(obj.rider_id)
        if not loc:
            return None

        distance_km = None
        if obj.destination_lat is not None and obj.destination_lng is not None:
            distance_km = haversine_distance_km(
                loc['lat'], loc['lng'],
                float(obj.destination_lat), float(obj.destination_lng)
            )

        return {
            'lat': loc['lat'],
            'lng': loc['lng'],
            'heading': loc['heading'],
            'speed_kmh': loc['speed_kmh'],
            'remaining_distance_km': distance_km,
            'updated_at': loc['updated_at'],
        }


class RiderLocationPingSerializer(serializers.Serializer):
    lat = serializers.FloatField(required=True)
    lng = serializers.FloatField(required=True)
    heading = serializers.FloatField(required=False, default=0.0)
    speed_kmh = serializers.FloatField(required=False, default=0.0)


class DispatchAssignSerializer(serializers.Serializer):
    rider_id = serializers.IntegerField(required=True)


class DispatchStatusUpdateSerializer(serializers.Serializer):
    status = serializers.CharField(required=True)
    rider_notes = serializers.CharField(required=False, allow_blank=True, default="")

