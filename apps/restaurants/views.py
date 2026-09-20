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
)
from .services import (
    restaurant_create,
    branch_create,
    restaurant_update,
    branch_update,
)
from .serializers import (
    RestaurantListSerializer,
    RestaurantDetailSerializer,
    RestaurantCreateSerializer,
    BranchSerializer,
    BranchCreateSerializer,
)


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
                name=serializer.validated_data['name'],
                branch_code=serializer.validated_data['branch_code'],
                manager=serializer.validated_data.get('manager'),
                phone_number=serializer.validated_data.get('phone_number', ''),
                email=serializer.validated_data.get('email', ''),
                address_line=serializer.validated_data.get('address_line', ''),
                city=serializer.validated_data.get('city', 'Kathmandu'),
                state=serializer.validated_data.get('state', 'Bagmati'),
                postal_code=serializer.validated_data.get('postal_code', ''),
                is_main_branch=serializer.validated_data.get('is_main_branch', False),
                is_active=serializer.validated_data.get('is_active', True),
                accepting_orders=serializer.validated_data.get('accepting_orders', True),
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

