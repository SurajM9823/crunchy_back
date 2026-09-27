from decimal import Decimal
from django.db.models import Q
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser

from apps.restaurants.permissions import IsOutletAdminOrStaff, IsOutletAdminOnly
from apps.restaurants.models import Branch
from .models import (
    InventoryItem,
    InventoryCategory,
    Supplier,
    PurchaseInvoice,
    StockMovementLedger,
)
from .selectors import (
    list_inventory_for_branch_with_metrics,
    list_inventory_for_branch,
    get_inventory_item_by_id,
    list_low_stock_items,
    list_recipes_for_product,
    list_stock_transactions,
    list_suppliers_for_branch,
    list_inventory_categories,
    list_purchase_invoices,
    get_purchase_invoice_by_id,
    list_stock_movements,
)
from .services import (
    inventory_item_create,
    inventory_item_restock,
    purchase_invoice_create,
    inventory_reconcile_audit,
    supplier_create,
    recipe_item_create,
)
from .serializers import (
    InventoryCategorySerializer,
    SupplierSerializer,
    SupplierCreateSerializer,
    InventoryItemSerializer,
    InventoryItemCreateUpdateSerializer,
    InventoryRestockRequestSerializer,
    PurchaseInvoiceSerializer,
    PurchaseInvoiceCreateSerializer,
    StockMovementLedgerSerializer,
    InventoryAuditReconcileRequestSerializer,
    RecipeItemSerializer,
    RecipeItemCreateSerializer,
    StockTransactionSerializer,
)


def _get_scoped_branch(request, outlet_id=None):
    """
    Tenant isolation helper to securely resolve the active outlet branch.
    """
    user = request.user

    # If explicit outlet_id passed in query or body
    target_outlet_id = outlet_id or request.query_params.get('outlet_id') or request.query_params.get('branch_id')

    if target_outlet_id:
        s = str(target_outlet_id).strip()
        qs = Branch.objects.filter(Q(id=s) if s.isdigit() else Q(branch_code=s) | Q(id=s))
        if user.is_superuser:
            b = qs.first()
            if b:
                return b
        elif hasattr(user, 'restaurant') and user.restaurant:
            b = qs.filter(restaurant=user.restaurant).first()
            if b:
                return b
        elif hasattr(user, 'branch') and user.branch:
            if str(user.branch.id) == s or user.branch.branch_code == s:
                return user.branch

    # Fallback to user's assigned branch or restaurant brand
    if getattr(user, 'branch', None):
        return user.branch
    elif hasattr(user, 'employee_profile') and user.employee_profile and user.employee_profile.assigned_outlet:
        return user.employee_profile.assigned_outlet
    elif getattr(user, 'restaurant', None):
        return user.restaurant.branches.first()
    elif user.is_superuser:
        return Branch.objects.first()

    return None


class SupplierListCreateAPIView(APIView):
    """
    GET /api/v1/inventory/suppliers/?search=...
    POST /api/v1/inventory/suppliers/
    Select2 live search & auto-creation for suppliers.
    """
    permission_classes = [IsAuthenticated, IsOutletAdminOrStaff]

    def get(self, request):
        branch = _get_scoped_branch(request)
        if not branch:
            return Response({"detail": "No outlet identified."}, status=status.HTTP_404_NOT_FOUND)

        search = request.query_params.get('search', '')
        suppliers = list_suppliers_for_branch(branch_id=branch.id, search=search)
        return Response(SupplierSerializer(suppliers, many=True).data, status=status.HTTP_200_OK)

    def post(self, request):
        branch = _get_scoped_branch(request)
        if not branch:
            return Response({"detail": "No outlet identified."}, status=status.HTTP_404_NOT_FOUND)

        serializer = SupplierCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        supplier = supplier_create(
            branch=branch,
            **serializer.validated_data
        )
        return Response(SupplierSerializer(supplier).data, status=status.HTTP_201_CREATED)


class InventoryCategoryListCreateAPIView(APIView):
    """
    GET /api/v1/inventory/categories/?search=...
    POST /api/v1/inventory/categories/
    Select2 live search & auto-creation for categories.
    """
    permission_classes = [IsAuthenticated, IsOutletAdminOrStaff]

    def get(self, request):
        search = request.query_params.get('search', '')
        cats = list_inventory_categories(search=search)
        return Response(InventoryCategorySerializer(cats, many=True).data, status=status.HTTP_200_OK)

    def post(self, request):
        name = request.data.get('name', '').strip()
        if not name:
            return Response({"detail": "Category name is required."}, status=status.HTTP_400_BAD_REQUEST)

        cat, _ = InventoryCategory.objects.get_or_create(
            name=name,
            defaults={'description': request.data.get('description', '')}
        )
        return Response(InventoryCategorySerializer(cat).data, status=status.HTTP_201_CREATED)


class InventoryItemListCreateAPIView(APIView):
    """
    GET /api/v1/inventory/items/ -> List stock items catalog with summary metrics (count, low_stock, valuation).
    POST /api/v1/inventory/items/ -> Register a new raw ingredient / stock SKU.
    """
    permission_classes = [IsAuthenticated, IsOutletAdminOrStaff]

    def get(self, request):
        branch = _get_scoped_branch(request)
        if not branch:
            return Response({"detail": "No outlet identified."}, status=status.HTTP_404_NOT_FOUND)

        search = request.query_params.get('search')
        category_id = request.query_params.get('category_id')
        category_name = request.query_params.get('category')
        low_stock = request.query_params.get('low_stock') == 'true'

        result = list_inventory_for_branch_with_metrics(
            branch_id=branch.id,
            search=search,
            category_id=category_id,
            category_name=category_name,
            low_stock_only=low_stock,
        )

        serialized_items = InventoryItemSerializer(result['queryset'], many=True).data

        # Return standardized envelope containing summary metrics and results
        return Response({
            "status": "success",
            "data": {
                "count": result['count'],
                "low_stock_count": result['low_stock_count'],
                "total_valuation": float(result['total_valuation']),
                "results": serialized_items,
            }
        }, status=status.HTTP_200_OK)

    def post(self, request):
        branch = _get_scoped_branch(request)
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


class PurchaseInvoiceListCreateAPIView(APIView):
    """
    GET /api/v1/inventory/purchases/ -> List inward purchase invoices.
    POST /api/v1/inventory/purchases/ -> Record inward procurement bill with atomic stock sync.
    Supports idempotency via Idempotency-Key header or idempotency_key payload.
    """
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    permission_classes = [IsAuthenticated, IsOutletAdminOrStaff]

    def get(self, request):
        branch = _get_scoped_branch(request)
        if not branch:
            return Response({"detail": "No outlet identified."}, status=status.HTTP_404_NOT_FOUND)

        search = request.query_params.get('search')
        supplier_id = request.query_params.get('supplier_id')
        purchases = list_purchase_invoices(branch_id=branch.id, search=search, supplier_id=supplier_id)
        return Response(PurchaseInvoiceSerializer(purchases, many=True).data, status=status.HTTP_200_OK)

    def post(self, request):
        # Extract Idempotency key from header if not in body
        idempotency_key = request.headers.get('Idempotency-Key') or request.data.get('idempotency_key')

        branch = _get_scoped_branch(request, outlet_id=request.data.get('outlet_id'))
        if not branch:
            return Response({"detail": "No outlet identified."}, status=status.HTTP_404_NOT_FOUND)

        serializer = PurchaseInvoiceCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        final_idempotency = idempotency_key or data.get('idempotency_key')

        invoice = purchase_invoice_create(
            branch=branch,
            invoice_number=data['invoice_number'],
            supplier_name=data.get('supplier_name', ''),
            supplier_phone=data.get('supplier_phone', ''),
            supplier_id=data.get('supplier_id'),
            purchase_date=data.get('purchase_date'),
            subtotal=data.get('subtotal', Decimal('0.00')),
            discount_amount=data.get('discount_amount', Decimal('0.00')),
            total_amount=data.get('total_amount', Decimal('0.00')),
            paid_amount=data.get('paid_amount', Decimal('0.00')),
            due_amount=data.get('due_amount', Decimal('0.00')),
            payment_status=data.get('payment_status', 'PAID'),
            payment_method=data.get('payment_method', 'CASH'),
            notes=data.get('notes', ''),
            document=request.FILES.get('document') or data.get('document'),
            document_name=data.get('document_name', ''),
            received_by=request.user,
            idempotency_key=final_idempotency,
            items_data=data.get('items', []),
        )

        return Response(PurchaseInvoiceSerializer(invoice).data, status=status.HTTP_201_CREATED)


class PurchaseInvoiceDetailAPIView(APIView):
    """
    GET /api/v1/inventory/purchases/<str:purchase_id>/
    """
    permission_classes = [IsAuthenticated, IsOutletAdminOrStaff]

    def get(self, request, purchase_id):
        branch = _get_scoped_branch(request)
        purchase = get_purchase_invoice_by_id(purchase_id, branch_id=branch.id if branch else None)
        if not purchase:
            return Response({"detail": "Purchase invoice not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(PurchaseInvoiceSerializer(purchase).data, status=status.HTTP_200_OK)


class InventoryAuditReconcileAPIView(APIView):
    """
    POST /api/v1/inventory/audits/reconcile/
    Reconciles physical stock counts against system stock balances.
    """
    permission_classes = [IsAuthenticated, IsOutletAdminOnly]

    def post(self, request):
        serializer = InventoryAuditReconcileRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        branch = _get_scoped_branch(request, outlet_id=data.get('outlet_id'))
        if not branch:
            return Response({"detail": "No outlet identified."}, status=status.HTTP_404_NOT_FOUND)

        reconciled = inventory_reconcile_audit(
            branch=branch,
            items_data=data.get('items', []),
            performed_by=request.user,
        )

        return Response({
            "status": "success",
            "message": f"Successfully reconciled {len(reconciled)} inventory items.",
            "reconciled": reconciled,
        }, status=status.HTTP_200_OK)


class StockMovementLedgerListAPIView(APIView):
    """
    GET /api/v1/inventory/movements/?item_id=...
    Lists immutable stock ledger movements for this outlet.
    """
    permission_classes = [IsAuthenticated, IsOutletAdminOrStaff]

    def get(self, request):
        branch = _get_scoped_branch(request)
        if not branch:
            return Response({"detail": "No outlet identified."}, status=status.HTTP_404_NOT_FOUND)

        item_id = request.query_params.get('item_id')
        movements = list_stock_movements(branch_id=branch.id, item_id=item_id)
        return Response(StockMovementLedgerSerializer(movements, many=True).data, status=status.HTTP_200_OK)


class LowStockAlertAPIView(APIView):
    """
    GET /api/v1/inventory/low-stock/
    Returns items currently below their minimum threshold.
    """
    permission_classes = [IsAuthenticated, IsOutletAdminOrStaff]

    def get(self, request):
        branch = _get_scoped_branch(request)
        if not branch:
            return Response({"detail": "No outlet identified."}, status=status.HTTP_404_NOT_FOUND)

        items = list_low_stock_items(branch.id)
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
        branch = _get_scoped_branch(request)
        if not branch:
            return Response({"detail": "No outlet identified."}, status=status.HTTP_404_NOT_FOUND)

        item_id = request.query_params.get('item_id')
        txns = list_stock_transactions(branch_id=branch.id, item_id=item_id)
        return Response(StockTransactionSerializer(txns, many=True).data, status=status.HTTP_200_OK)
