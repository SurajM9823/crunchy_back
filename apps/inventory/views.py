from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from django.core.exceptions import ValidationError

from apps.restaurants.permissions import IsOutletAdminOrStaff, IsOutletAdminOnly
from apps.restaurants.models import Branch
from .models import InventoryItem
from .selectors import (
    list_inventory_for_branch,
    get_inventory_item_by_id,
    list_low_stock_items,
    list_recipes_for_product,
    list_stock_transactions,
)
from .services import (
    inventory_item_create,
    inventory_item_restock,
    recipe_item_create,
)
from .serializers import (
    InventoryItemSerializer,
    InventoryItemCreateUpdateSerializer,
    InventoryRestockRequestSerializer,
    RecipeItemSerializer,
    RecipeItemCreateSerializer,
    StockTransactionSerializer,
)


class InventoryItemListCreateAPIView(APIView):
    """
    GET /api/v1/inventory/items/ -> List all stock items for current outlet.
    POST /api/v1/inventory/items/ -> Register a new raw ingredient / stock SKU.
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

        category_id = request.query_params.get('category_id')
        low_stock = request.query_params.get('low_stock') == 'true'

        items = list_inventory_for_branch(
            branch_id=branch.id,
            category_id=category_id,
            low_stock_only=low_stock,
        )
        return Response(InventoryItemSerializer(items, many=True).data, status=status.HTTP_200_OK)

    def post(self, request):
        branch = self._get_branch(request)
        if not branch:
            return Response({"detail": "No outlet identified."}, status=status.HTTP_404_NOT_FOUND)

        serializer = InventoryItemCreateUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        item = inventory_item_create(
            branch=branch,
            **serializer.validated_data
        )
        return Response(InventoryItemSerializer(item).data, status=status.HTTP_201_CREATED)


class InventoryItemDetailAPIView(APIView):
    """
    GET /api/v1/inventory/items/<int:item_id>/
    PATCH /api/v1/inventory/items/<int:item_id>/
    """
    permission_classes = [IsAuthenticated, IsOutletAdminOrStaff]

    def get(self, request, item_id):
        item = get_inventory_item_by_id(item_id)
        if not item:
            return Response({"detail": "Inventory item not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(InventoryItemSerializer(item).data, status=status.HTTP_200_OK)

    def patch(self, request, item_id):
        item = get_inventory_item_by_id(item_id)
        if not item:
            return Response({"detail": "Inventory item not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = InventoryItemCreateUpdateSerializer(item, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        for attr, value in serializer.validated_data.items():
            setattr(item, attr, value)
        item.save()
        return Response(InventoryItemSerializer(item).data, status=status.HTTP_200_OK)


class InventoryRestockAPIView(APIView):
    """
    POST /api/v1/inventory/items/<int:item_id>/restock/
    Logs shipment intake and increments stock atomically.
    """
    permission_classes = [IsAuthenticated, IsOutletAdminOnly]

    def post(self, request, item_id):
        item = get_inventory_item_by_id(item_id)
        if not item:
            return Response({"detail": "Inventory item not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = InventoryRestockRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        updated_item = inventory_item_restock(
            item=item,
            quantity=serializer.validated_data['quantity'],
            cost_per_unit=serializer.validated_data.get('cost_per_unit'),
            performed_by=request.user,
            notes=serializer.validated_data.get('notes', ''),
        )
        return Response({
            "message": f"Successfully restocked {serializer.validated_data['quantity']} {item.unit} to {item.name}.",
            "item": InventoryItemSerializer(updated_item).data,
        }, status=status.HTTP_200_OK)


class LowStockAlertAPIView(APIView):
    """
    GET /api/v1/inventory/low-stock/
    Returns items currently below their minimum threshold.
    """
    permission_classes = [IsAuthenticated, IsOutletAdminOrStaff]

    def get(self, request):
        branch_id = request.user.branch_id
        if not branch_id and request.user.is_superuser:
            first_b = Branch.objects.first()
            if first_b:
                branch_id = first_b.id

        if not branch_id:
            return Response({"detail": "No outlet identified."}, status=status.HTTP_404_NOT_FOUND)

        items = list_low_stock_items(branch_id)
        return Response(InventoryItemSerializer(items, many=True).data, status=status.HTTP_200_OK)


class RecipeListCreateAPIView(APIView):
    """
    GET /api/v1/inventory/recipes/?product_id=<prod-id>
    POST /api/v1/inventory/recipes/
    """
    permission_classes = [IsAuthenticated, IsOutletAdminOnly]

    def get(self, request):
        product_id = request.query_params.get('product_id')
        if not product_id:
            return Response({"detail": "product_id query parameter is required."}, status=status.HTTP_400_BAD_REQUEST)

        recipes = list_recipes_for_product(product_id)
        return Response(RecipeItemSerializer(recipes, many=True).data, status=status.HTTP_200_OK)

    def post(self, request):
        serializer = RecipeItemCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        recipe = recipe_item_create(**serializer.validated_data)
        return Response(RecipeItemSerializer(recipe).data, status=status.HTTP_201_CREATED)


class StockTransactionHistoryAPIView(APIView):
    """
    GET /api/v1/inventory/transactions/?item_id=<id>
    Audit ledger of all stock additions and order deductions.
    """
    permission_classes = [IsAuthenticated, IsOutletAdminOrStaff]

    def get(self, request):
        branch_id = request.user.branch_id
        if not branch_id and request.user.is_superuser:
            first_b = Branch.objects.first()
            if first_b:
                branch_id = first_b.id

        if not branch_id:
            return Response({"detail": "No outlet identified."}, status=status.HTTP_404_NOT_FOUND)

        item_id = request.query_params.get('item_id')
        txns = list_stock_transactions(branch_id=branch_id, item_id=item_id)
        return Response(StockTransactionSerializer(txns, many=True).data, status=status.HTTP_200_OK)

