import math
import json
import time
from decimal import Decimal
from django.core.cache import cache
from django.utils import timezone
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer


RIDER_GPS_CACHE_TTL = 60  # 60 seconds ephemeral storage in Redis


def haversine_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Computes great-circle distance between two GPS coordinates using the Haversine formula.
    Returns distance in kilometers.
    """
    R = 6371.0  # Earth radius in kilometers

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (math.sin(delta_phi / 2.0) ** 2 +
         math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2)
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))

    return round(R * c, 2)


def update_rider_live_location(
    rider_id: int,
    lat: float,
    lng: float,
    heading: float = 0.0,
    speed_kmh: float = 0.0,
) -> dict:
    """
    High-Scale Ephemeral GPS Update (Rule 13 - 10k Concurrency):
    Stores live coordinates in Redis cache without disk write thrashing.
    """
    payload = {
        'rider_id': rider_id,
        'lat': float(lat),
        'lng': float(lng),
        'heading': float(heading),
        'speed_kmh': float(speed_kmh),
        'updated_at': int(time.time()),
    }
    cache_key = f"rider:{rider_id}:location"
    cache.set(cache_key, payload, timeout=RIDER_GPS_CACHE_TTL)
    return payload


def get_rider_live_location(rider_id: int) -> dict:
    """
    Retrieves live rider coordinates from Redis in sub-5ms.
    """
    cache_key = f"rider:{rider_id}:location"
    return cache.get(cache_key)


def broadcast_live_delivery_tracking(
    order_id: int,
    branch_id: int,
    rider_id: int,
    lat: float,
    lng: float,
    heading: float = 0.0,
    destination_lat: float = None,
    destination_lng: float = None,
):
    """
    Broadcasts real-time GPS coordinate movement over WebSockets
    to the customer smartphone tracking screen with Zero Page Reload!
    """
    channel_layer = get_channel_layer()
    if not channel_layer:
        return

    distance_km = None
    if destination_lat is not None and destination_lng is not None:
        distance_km = haversine_distance_km(lat, lng, destination_lat, destination_lng)

    event_payload = {
        'type': 'order_event',
        'event': 'RIDER_LOCATION_UPDATED',
        'order_id': order_id,
        'rider_id': rider_id,
        'lat': float(lat),
        'lng': float(lng),
        'heading': float(heading),
        'remaining_distance_km': distance_km,
        'timestamp': timezone.now().isoformat(),
    }

    # Stream to customer phone
    async_to_sync(channel_layer.group_send)(f"order_{order_id}", event_payload)

    # Stream to cashier/dispatcher POS
    async_to_sync(channel_layer.group_send)(f"outlet_{branch_id}_operations", event_payload)

