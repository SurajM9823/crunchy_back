from decimal import Decimal
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.core.exceptions import ValidationError

from apps.restaurants.permissions import IsOutletAdminOrStaff, IsOutletAdminOnly
from apps.restaurants.models import Branch
from apps.orders.selectors import get_order_by_id, get_order_by_number
from .models import DeliveryZone, RiderProfile, DeliveryDispatch
from .selectors import (
    list_zones_for_branch,
    list_riders_for_branch,
    get_dispatch_by_id,
    get_dispatch_for_order,
    list_active_dispatches_for_branch,
)
from .services import (
    delivery_zone_create,
    rider_profile_create,
    assign_rider_to_delivery,
    rider_update_dispatch_status,
)
from .geo_tracker import (
    update_rider_live_location,
    broadcast_live_delivery_tracking,
)
from .serializers import (
    DeliveryZoneSerializer,
    DeliveryZoneCreateUpdateSerializer,
    RiderProfileSerializer,
    DeliveryDispatchSerializer,
    RiderLocationPingSerializer,
    DispatchAssignSerializer,
    DispatchStatusUpdateSerializer,
)


class DeliveryZoneListCreateAPIView(APIView):
    """
    GET /api/v1/delivery/zones/ -> List active delivery zones.
    POST /api/v1/delivery/zones/ -> Create a new delivery zone.
    """
    permission_classes = [IsAuthenticated, IsOutletAdminOrStaff]

    def _get_branch(self, request):
        user = request.user
        if user.branch:
            return user.branch
        elif user.restaurant:
            return user.restaurant.branches.first()
        elif user.is_superuser:
            branch_id = request.query_params.get('branch_id')
            return Branch.objects.filter(id=branch_id).first() if branch_id else Branch.objects.first()
        return None

    def get(self, request):
        branch = self._get_branch(request)
        if not branch:
            return Response({"detail": "No outlet identified."}, status=status.HTTP_404_NOT_FOUND)

        zones = list_zones_for_branch(branch.id)
        return Response(DeliveryZoneSerializer(zones, many=True).data, status=status.HTTP_200_OK)

    def post(self, request):
        branch = self._get_branch(request)
        if not branch:
            return Response({"detail": "No outlet identified."}, status=status.HTTP_404_NOT_FOUND)

        serializer = DeliveryZoneCreateUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        zone = delivery_zone_create(
            branch=branch,
            **serializer.validated_data
        )
        return Response(DeliveryZoneSerializer(zone).data, status=status.HTTP_201_CREATED)


class RiderListCreateAPIView(APIView):
    """
    GET /api/v1/delivery/riders/ -> List fleet riders and their live status.
    """
    permission_classes = [IsAuthenticated, IsOutletAdminOrStaff]

    def get(self, request):
        branch_id = request.user.branch_id
        if not branch_id and request.user.restaurant:
            first_b = request.user.restaurant.branches.first()
            if first_b:
                branch_id = first_b.id
        elif not branch_id and request.user.is_superuser:
            first_b = Branch.objects.first()
            if first_b:
                branch_id = first_b.id

        if not branch_id:
            return Response({"detail": "No outlet identified."}, status=status.HTTP_404_NOT_FOUND)

        riders = list_riders_for_branch(branch_id=branch_id)
        return Response(RiderProfileSerializer(riders, many=True).data, status=status.HTTP_200_OK)


class DeliveryDispatchListAPIView(APIView):
    """
    GET /api/v1/delivery/dispatches/ -> Live active dispatch board for branch dispatcher.
    """
    permission_classes = [IsAuthenticated, IsOutletAdminOrStaff]

    def get(self, request):
        branch_id = request.user.branch_id
        if not branch_id and request.user.restaurant:
            first_b = request.user.restaurant.branches.first()
            if first_b:
                branch_id = first_b.id
        elif not branch_id and request.user.is_superuser:
            first_b = Branch.objects.first()
            if first_b:
                branch_id = first_b.id

        if not branch_id:
            return Response({"detail": "No outlet identified."}, status=status.HTTP_404_NOT_FOUND)

        dispatches = list_active_dispatches_for_branch(branch_id)
        return Response(DeliveryDispatchSerializer(dispatches, many=True).data, status=status.HTTP_200_OK)


class DeliveryDispatchAssignAPIView(APIView):
    """
    POST /api/v1/delivery/dispatches/<int:dispatch_id>/assign/
    Assign a rider to an order dispatch.
    """
    permission_classes = [IsAuthenticated, IsOutletAdminOnly]

    def post(self, request, dispatch_id):
        dispatch = get_dispatch_by_id(dispatch_id)
        if not dispatch:
            return Response({"detail": "Delivery dispatch not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = DispatchAssignSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        rider = RiderProfile.objects.filter(id=serializer.validated_data['rider_id'], is_active=True).first()
        if not rider:
            return Response({"detail": "Rider not found."}, status=status.HTTP_404_NOT_FOUND)

        updated_dispatch = assign_rider_to_delivery(dispatch=dispatch, rider=rider)
        return Response(DeliveryDispatchSerializer(updated_dispatch).data, status=status.HTTP_200_OK)


class DeliveryDispatchStatusAPIView(APIView):
    """
    POST /api/v1/delivery/dispatches/<int:dispatch_id>/status/
    Rider transitions delivery status (e.g. PICKED_UP, DELIVERED).
    Dispatches live WebSocket event with Zero Page Reload!
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, dispatch_id):
        dispatch = get_dispatch_by_id(dispatch_id)
        if not dispatch:
            return Response({"detail": "Delivery dispatch not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = DispatchStatusUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        updated_dispatch = rider_update_dispatch_status(
            dispatch=dispatch,
            new_status=serializer.validated_data['status'],
            rider_notes=serializer.validated_data.get('rider_notes', ''),
        )
        return Response(DeliveryDispatchSerializer(updated_dispatch).data, status=status.HTTP_200_OK)


class RiderLocationPingAPIView(APIView):
    """
    POST /api/v1/delivery/rider/location/
    High-Scale Ephemeral GPS Ping (Rule 13 - 10k Concurrency):
    Rider smartphone app broadcasts live coordinates into Redis cache (60s TTL).
    Streams live map movement to customer phone without database write thrashing.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = RiderLocationPingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        rider_profile = getattr(request.user, 'rider_profile', None)
        if not rider_profile:
            return Response({"detail": "Authenticated user is not registered as a rider."}, status=status.HTTP_403_FORBIDDEN)

        # 1. Update Redis ephemeral cache
        loc = update_rider_live_location(
            rider_id=rider_profile.id,
            lat=data['lat'],
            lng=data['lng'],
            heading=data.get('heading', 0.0),
            speed_kmh=data.get('speed_kmh', 0.0),
        )

        # 2. If rider is on active delivery, broadcast to customer map
        active_dispatch = (
            DeliveryDispatch.objects
            .filter(rider=rider_profile, status__in=['ASSIGNED', 'ACCEPTED', 'PICKED_UP', 'ARRIVED'])
            .first()
        )
        if active_dispatch:
            broadcast_live_delivery_tracking(
                order_id=active_dispatch.order_id,
                branch_id=active_dispatch.branch_id,
                rider_id=rider_profile.id,
                lat=data['lat'],
                lng=data['lng'],
                heading=data.get('heading', 0.0),
                destination_lat=float(active_dispatch.destination_lat) if active_dispatch.destination_lat else None,
                destination_lng=float(active_dispatch.destination_lng) if active_dispatch.destination_lng else None,
            )

        return Response({"status": "ping_received", "location": loc}, status=status.HTTP_200_OK)


class CustomerDeliveryTrackingAPIView(APIView):
    """
    GET /api/v1/delivery/dispatches/<str:identifier>/track/
    Public Customer Live Tracking:
    Displays door-to-door transit status, remaining distance, and live rider location.
    """
    permission_classes = [AllowAny]

    def get(self, request, identifier):
        order = None
        if str(identifier).isdigit():
            order = get_order_by_id(int(identifier))
        if not order:
            order = get_order_by_number(str(identifier))

        if not order:
            return Response({"detail": "Order not found."}, status=status.HTTP_404_NOT_FOUND)

        dispatch = getattr(order, 'delivery_dispatch', None)
        if not dispatch:
            return Response({"detail": "No delivery dispatch registered for this order."}, status=status.HTTP_404_NOT_FOUND)

        return Response(DeliveryDispatchSerializer(dispatch).data, status=status.HTTP_200_OK)

