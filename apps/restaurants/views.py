from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.core.exceptions import ValidationError

from .models import Restaurant, Branch
from .selectors import (
    get_restaurant_by_id,
    get_restaurant_by_slug,
    list_restaurants,
    list_branches_by_restaurant,
    get_branch_by_id,
    get_branch_by_code,
    get_outlet_live_status,
)
from .services import (
    restaurant_create,
    branch_create,
    restaurant_update,
    branch_update,
    outlet_update_operational_status,
)
from .serializers import (
    RestaurantListSerializer,
    RestaurantDetailSerializer,
    RestaurantCreateSerializer,
    BranchSerializer,
    BranchCreateSerializer,
)
from .permissions import IsOutletAdminOrStaff, IsOutletAdminOnly


class RestaurantListCreateAPIView(APIView):
    """
    GET /api/v1/restaurants/ -> List restaurant brands.
    POST /api/v1/restaurants/ -> Create a new restaurant brand (Superuser only).
    """
    def get_permissions(self):
        if self.request.method == 'GET':
            return [AllowAny()]
        return [IsAuthenticated()]

    def get(self, request):
        is_superuser = request.user.is_authenticated and request.user.is_superuser
        restaurants = list_restaurants(active_only=not is_superuser)
        serializer = RestaurantListSerializer(restaurants, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        if not request.user.is_superuser:
            return Response(
                {"detail": "Only Superusers can create new Restaurant Brands."},
                status=status.HTTP_403_FORBIDDEN
            )

        serializer = RestaurantCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            restaurant = restaurant_create(
                name=serializer.validated_data['name'],
                admin_user=serializer.validated_data.get('admin', request.user),
                pan_number=serializer.validated_data.get('pan_number', ''),
                phone=serializer.validated_data.get('phone', ''),
                email=serializer.validated_data.get('email', ''),
                website=serializer.validated_data.get('website', ''),
                logo_url=serializer.validated_data.get('logo_url', ''),
                description=serializer.validated_data.get('description', ''),
                is_active=serializer.validated_data.get('is_active', True),
            )
            output = RestaurantDetailSerializer(restaurant)
            return Response(output.data, status=status.HTTP_201_CREATED)
        except ValidationError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class RestaurantDetailAPIView(APIView):
    """
    GET /api/v1/restaurants/<id_or_slug>/ -> Detailed view with all franchise branches.
    PATCH /api/v1/restaurants/<id_or_slug>/ -> Update brand details.
    """
    def get_permissions(self):
        if self.request.method == 'GET':
            return [AllowAny()]
        return [IsAuthenticated()]

    def _get_restaurant(self, identifier):
        if str(identifier).isdigit():
            return get_restaurant_by_id(int(identifier))
        return get_restaurant_by_slug(str(identifier))

    def get(self, request, identifier):
        restaurant = self._get_restaurant(identifier)
        if not restaurant:
            return Response({"detail": "Restaurant brand not found."}, status=status.HTTP_404_NOT_FOUND)
        serializer = RestaurantDetailSerializer(restaurant)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def patch(self, request, identifier):
        restaurant = self._get_restaurant(identifier)
        if not restaurant:
            return Response({"detail": "Restaurant brand not found."}, status=status.HTTP_404_NOT_FOUND)

        # Allow Superuser or Brand Admin
        if not (request.user.is_superuser or restaurant.admin_id == request.user.id):
            return Response({"detail": "Permission denied."}, status=status.HTTP_403_FORBIDDEN)

        serializer = RestaurantCreateSerializer(restaurant, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        updated_restaurant = restaurant_update(restaurant, **serializer.validated_data)
        output = RestaurantDetailSerializer(updated_restaurant)
        return Response(output.data, status=status.HTTP_200_OK)


class BranchListCreateAPIView(APIView):
    """
    GET /api/v1/restaurants/<restaurant_id>/branches/ -> List all outlets for a brand.
    POST /api/v1/restaurants/<restaurant_id>/branches/ -> Create a new outlet under this brand.
    """
    def get_permissions(self):
        if self.request.method == 'GET':
            return [AllowAny()]
        return [IsAuthenticated()]

    def get(self, request, restaurant_id):
        restaurant = get_restaurant_by_id(restaurant_id)
        if not restaurant:
            return Response({"detail": "Restaurant brand not found."}, status=status.HTTP_404_NOT_FOUND)
        branches = list_branches_by_restaurant(restaurant_id)
        serializer = BranchSerializer(branches, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request, restaurant_id):
        restaurant = get_restaurant_by_id(restaurant_id)
        if not restaurant:
            return Response({"detail": "Restaurant brand not found."}, status=status.HTTP_404_NOT_FOUND)

        # Only Superuser or Restaurant Admin can add branches
        if not (request.user.is_superuser or restaurant.admin_id == request.user.id):
            return Response({"detail": "Permission denied."}, status=status.HTTP_403_FORBIDDEN)

        serializer = BranchCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            branch = branch_create(
                restaurant=restaurant,
                **serializer.validated_data
            )
            output = BranchSerializer(branch)
            return Response(output.data, status=status.HTTP_201_CREATED)
        except ValidationError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class BranchDetailAPIView(APIView):
    """
    GET /api/v1/branches/<branch_id_or_code>/ -> Retrieve branch outlet details.
    PATCH /api/v1/branches/<branch_id_or_code>/ -> Update branch outlet.
    """
    def get_permissions(self):
        if self.request.method == 'GET':
            return [AllowAny()]
        return [IsAuthenticated()]

    def _get_branch(self, identifier):
        if str(identifier).isdigit():
            return get_branch_by_id(int(identifier))
        return get_branch_by_code(str(identifier))

    def get(self, request, identifier):
        branch = self._get_branch(identifier)
        if not branch:
            return Response({"detail": "Branch outlet not found."}, status=status.HTTP_404_NOT_FOUND)
        serializer = BranchSerializer(branch)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def patch(self, request, identifier):
        branch = self._get_branch(identifier)
        if not branch:
            return Response({"detail": "Branch outlet not found."}, status=status.HTTP_404_NOT_FOUND)

        # Superuser, Restaurant Admin, or Branch Manager
        is_authorized = (
            request.user.is_superuser
            or branch.restaurant.admin_id == request.user.id
            or (branch.manager_id and branch.manager_id == request.user.id)
        )
        if not is_authorized:
            return Response({"detail": "Permission denied."}, status=status.HTTP_403_FORBIDDEN)

        serializer = BranchCreateSerializer(branch, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        updated_branch = branch_update(branch, **serializer.validated_data)
        output = BranchSerializer(updated_branch)
        return Response(output.data, status=status.HTTP_200_OK)


# --------------------------------------------------------------------------
# Outlet Admin Live Operations Endpoints (High-Scale 10k Concurrency)
# --------------------------------------------------------------------------

class OutletCurrentDetailAPIView(APIView):
    """
    GET /api/v1/outlets/me/
    Retrieves operational details of the outlet assigned to the currently authenticated
    Outlet Administrator (Branch Manager, Restaurant Owner, or Staff).
    """
    permission_classes = [IsAuthenticated, IsOutletAdminOrStaff]

    def get(self, request):
        user = request.user
        branch = None

        if user.branch:
            branch = user.branch
        elif user.restaurant:
            branch = user.restaurant.branches.first()

        if not branch and not user.is_superuser:
            return Response(
                {"detail": "No outlet is currently assigned to your account."},
                status=status.HTTP_404_NOT_FOUND
            )

        if not branch and user.is_superuser:
            # For superuser testing, return first branch or 404
            branch = Branch.objects.first()
            if not branch:
                return Response({"detail": "No branches exist yet."}, status=status.HTTP_404_NOT_FOUND)

        serializer = BranchSerializer(branch)
        return Response(serializer.data, status=status.HTTP_200_OK)


class OutletStatusToggleAPIView(APIView):
    """
    POST /api/v1/outlets/me/toggle-orders/
    Enables an Outlet Administrator to pause/resume kitchen orders or toggle
    fulfillment channels in emergency/rush situations.
    Busts Redis cache and broadcasts an OUTLET_STATUS_CHANGED event via Django Channels
    for ZERO-PAGE-RELOAD updates on all connected kiosks and customer phones.
    """
    permission_classes = [IsAuthenticated, IsOutletAdminOnly]

    def post(self, request):
        user = request.user
        branch = None

        if user.branch:
            branch = user.branch
        elif user.restaurant:
            branch = user.restaurant.branches.first()
        elif user.is_superuser:
            branch_id = request.data.get('branch_id')
            branch = get_branch_by_id(branch_id) if branch_id else Branch.objects.first()

        if not branch:
            return Response({"detail": "No outlet identified."}, status=status.HTTP_404_NOT_FOUND)

        accepting_orders = request.data.get('accepting_orders')
        is_active = request.data.get('is_active')
        channel_toggles = request.data.get('channels')

        updated_branch = outlet_update_operational_status(
            branch=branch,
            accepting_orders=accepting_orders if isinstance(accepting_orders, bool) else None,
            is_active=is_active if isinstance(is_active, bool) else None,
            channel_toggles=channel_toggles if isinstance(channel_toggles, dict) else None,
        )

        serializer = BranchSerializer(updated_branch)
        return Response({
            "message": "Outlet operational state updated and live WebSocket broadcast dispatched.",
            "outlet": serializer.data,
        }, status=status.HTTP_200_OK)


class OutletDashboardSummaryAPIView(APIView):
    """
    GET /api/v1/outlets/me/summary/
    High-Scale Redis Cache-Aside status summary (Rule 13).
    Serves instantaneous cached operational status to withstand 10,000+ requests.
    """
    permission_classes = [IsAuthenticated, IsOutletAdminOrStaff]

    def get(self, request):
        user = request.user
        branch_id = user.branch_id

        if not branch_id and user.restaurant:
            first_branch = user.restaurant.branches.first()
            if first_branch:
                branch_id = first_branch.id
        elif not branch_id and user.is_superuser:
            first_branch = Branch.objects.first()
            if first_branch:
                branch_id = first_branch.id

        if not branch_id:
            return Response({"detail": "No outlet identified."}, status=status.HTTP_404_NOT_FOUND)

        status_data = get_outlet_live_status(branch_id)
        if not status_data:
            return Response({"detail": "Outlet not found."}, status=status.HTTP_404_NOT_FOUND)

        return Response(status_data, status=status.HTTP_200_OK)

