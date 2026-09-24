from .models import DeliveryZone, RiderProfile, DeliveryDispatch, DispatchStatus, RiderStatus
from .geo_tracker import haversine_distance_km


def list_zones_for_branch(branch_id: int, active_only: bool = True):
    qs = DeliveryZone.objects.filter(branch_id=branch_id)
    if active_only:
        qs = qs.filter(is_active=True)
    return qs.order_by('radius_km')


def list_riders_for_branch(branch_id: int, status: str = None):
    qs = RiderProfile.objects.select_related('user', 'branch').filter(branch_id=branch_id, is_active=True)
    if status:
        qs = qs.filter(status=status)
    return qs.order_by('user__username')


def get_dispatch_by_id(dispatch_id: int) -> DeliveryDispatch:
    return (
        DeliveryDispatch.objects
        .select_related('order', 'branch', 'rider', 'rider__user', 'zone')
        .filter(id=dispatch_id)
        .first()
    )


def get_dispatch_for_order(order_id: int) -> DeliveryDispatch:
    return (
        DeliveryDispatch.objects
        .select_related('order', 'branch', 'rider', 'rider__user', 'zone')
        .filter(order_id=order_id)
        .first()
    )


def list_active_dispatches_for_branch(branch_id: int):
    return (
        DeliveryDispatch.objects
        .select_related('order', 'branch', 'rider', 'rider__user', 'zone')
        .filter(
            branch_id=branch_id,
            status__in=[
                DispatchStatus.UNASSIGNED,
                DispatchStatus.ASSIGNED,
                DispatchStatus.ACCEPTED,
                DispatchStatus.PICKED_UP,
                DispatchStatus.ARRIVED,
            ]
        )
        .order_by('-created_at')
    )


def find_eligible_delivery_zone(branch_id: int, lat: float, lng: float) -> DeliveryZone:
    """
    Finds the active delivery zone for a given destination coordinate.
    Returns DeliveryZone or None if outside service range.
    """
    zones = DeliveryZone.objects.filter(branch_id=branch_id, is_active=True).order_by('radius_km')
    for z in zones:
        dist = haversine_distance_km(float(z.center_lat), float(z.center_lng), float(lat), float(lng))
        if dist <= float(z.radius_km):
            return z
    return None

