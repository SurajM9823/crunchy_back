import uuid
from decimal import Decimal
from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from apps.orders.models import Order, OrderStatus, PaymentStatus, OrderStatusHistory
from .models import (
    DeliveryZone,
    RiderProfile,
    DeliveryDispatch,
    DispatchStatus,
    RiderStatus,
    VehicleType,
)
from .geo_tracker import haversine_distance_km


def generate_dispatch_id(branch) -> str:
    now = timezone.now()
    date_str = now.strftime('%y%m%d')
    suffix = uuid.uuid4().hex[:6].upper()
    return f"DSP-{branch.branch_code}-{date_str}-{suffix}"


@transaction.atomic
def delivery_zone_create(
    branch,
    name: str,
    center_lat: Decimal,
    center_lng: Decimal,
    radius_km: Decimal = Decimal('5.00'),
    delivery_fee: Decimal = Decimal('50.00'),
    min_order_amount: Decimal = Decimal('0.00'),
    free_delivery_threshold: Decimal = None,
    estimated_delivery_minutes: int = 35,
) -> DeliveryZone:
    zone = DeliveryZone(
        branch=branch,
        name=name.strip(),
        center_lat=Decimal(str(center_lat)),
        center_lng=Decimal(str(center_lng)),
        radius_km=Decimal(str(radius_km)),
        delivery_fee=Decimal(str(delivery_fee)),
        min_order_amount=Decimal(str(min_order_amount)),
        free_delivery_threshold=Decimal(str(free_delivery_threshold)) if free_delivery_threshold else None,
        estimated_delivery_minutes=estimated_delivery_minutes,
    )
    zone.save()
    return zone


@transaction.atomic
def rider_profile_create(
    user,
    branch,
    vehicle_type: str = VehicleType.MOTORCYCLE,
    vehicle_plate: str = "",
) -> RiderProfile:
    profile = RiderProfile(
        user=user,
        branch=branch,
        vehicle_type=vehicle_type,
        vehicle_plate=vehicle_plate.strip().upper(),
        status=RiderStatus.AVAILABLE,
    )
    profile.save()
    return profile


@transaction.atomic
def order_dispatch_create(
    order: Order,
    delivery_address: str,
    destination_lat: Decimal = None,
    destination_lng: Decimal = None,
    zone: DeliveryZone = None,
) -> DeliveryDispatch:
    """
    Creates a new delivery dispatch record for an order.
    """
    branch = order.branch

    # Auto-match zone if coordinates provided and no zone passed
    if not zone and destination_lat is not None and destination_lng is not None:
        for z in DeliveryZone.objects.filter(branch=branch, is_active=True):
            dist = haversine_distance_km(
                float(z.center_lat), float(z.center_lng),
                float(destination_lat), float(destination_lng)
            )
            if dist <= float(z.radius_km):
                zone = z
                break

    dispatch = DeliveryDispatch(
        dispatch_id=generate_dispatch_id(branch),
        order=order,
        branch=branch,
        zone=zone,
        delivery_address=delivery_address.strip(),
        destination_lat=Decimal(str(destination_lat)) if destination_lat is not None else None,
        destination_lng=Decimal(str(destination_lng)) if destination_lng is not None else None,
        status=DispatchStatus.UNASSIGNED,
    )
    dispatch.save()
    return dispatch


@transaction.atomic
def assign_rider_to_delivery(
    dispatch: DeliveryDispatch,
    rider: RiderProfile,
) -> DeliveryDispatch:
    """
    Assigns an available rider to an active delivery dispatch.
    """
    # Lock rows
    dispatch = DeliveryDispatch.objects.select_for_update().get(id=dispatch.id)
    rider = RiderProfile.objects.select_for_update().get(id=rider.id)

    dispatch.rider = rider
    dispatch.status = DispatchStatus.ASSIGNED
    dispatch.assigned_at = timezone.now()
    dispatch.save()

    rider.status = RiderStatus.BUSY
    rider.save(update_fields=['status', 'updated_at'])

    # Real-Time WebSocket broadcast
    channel_layer = get_channel_layer()
    if channel_layer:
        payload = {
            'type': 'order_event',
            'event': 'RIDER_ASSIGNED',
            'order_id': dispatch.order_id,
            'dispatch_id': dispatch.dispatch_id,
            'rider_name': rider.user.get_full_name() or rider.user.username,
            'rider_phone': rider.user.phone_number,
            'vehicle_plate': rider.vehicle_plate,
            'vehicle_type': rider.vehicle_type,
            'timestamp': timezone.now().isoformat(),
        }
        async_to_sync(channel_layer.group_send)(f"order_{dispatch.order_id}", payload)
        async_to_sync(channel_layer.group_send)(f"outlet_{dispatch.branch_id}_operations", payload)

    return dispatch


@transaction.atomic
def rider_update_dispatch_status(
    dispatch: DeliveryDispatch,
    new_status: str,
    rider_notes: str = "",
) -> DeliveryDispatch:
    """
    Rider transitions delivery status:
    ASSIGNED -> ACCEPTED -> PICKED_UP (Out for Delivery) -> ARRIVED -> DELIVERED.
    """
    dispatch = DeliveryDispatch.objects.select_for_update().get(id=dispatch.id)
    order = dispatch.order
    rider = dispatch.rider

    old_status = dispatch.status
    dispatch.status = new_status
    if rider_notes:
        dispatch.rider_notes = rider_notes.strip()

    now = timezone.now()

    if new_status == DispatchStatus.PICKED_UP:
        dispatch.picked_up_at = now
        order.status = OrderStatus.OUT_FOR_DELIVERY
        order.save(update_fields=['status', 'updated_at'])
        OrderStatusHistory.objects.create(
            order=order,
            from_status=OrderStatus.READY,
            to_status=OrderStatus.OUT_FOR_DELIVERY,
            notes=f"Picked up by rider {rider.vehicle_plate if rider else ''}",
        )

    elif new_status == DispatchStatus.DELIVERED:
        dispatch.delivered_at = now
        order.status = OrderStatus.COMPLETED
        order.payment_status = PaymentStatus.PAID
        order.save(update_fields=['status', 'payment_status', 'updated_at'])

        if rider:
            rider.status = RiderStatus.AVAILABLE
            rider.total_deliveries += 1
            rider.save(update_fields=['status', 'total_deliveries', 'updated_at'])

        OrderStatusHistory.objects.create(
            order=order,
            from_status=OrderStatus.OUT_FOR_DELIVERY,
            to_status=OrderStatus.COMPLETED,
            notes="Order delivered to customer",
        )

    elif new_status == DispatchStatus.FAILED:
        if rider:
            rider.status = RiderStatus.AVAILABLE
            rider.save(update_fields=['status', 'updated_at'])

    dispatch.save()

    # WebSocket Broadcast (Zero Page Reload)
    channel_layer = get_channel_layer()
    if channel_layer:
        payload = {
            'type': 'order_event',
            'event': 'DELIVERY_STATUS_CHANGED',
            'order_id': order.id,
            'dispatch_id': dispatch.dispatch_id,
            'status': dispatch.status,
            'timestamp': now.isoformat(),
        }
        async_to_sync(channel_layer.group_send)(f"order_{order.id}", payload)
        async_to_sync(channel_layer.group_send)(f"outlet_{dispatch.branch_id}_operations", payload)

    return dispatch

