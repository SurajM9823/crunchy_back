from decimal import Decimal
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.core.exceptions import ValidationError

from apps.restaurants.models import Branch
from apps.restaurants.permissions import IsOutletAdminOnly, IsOutletAdminOrStaff
from .models import Category, Product, OutletProductOverride
from .selectors import (
    list_categories,
    get_category_by_id,
    list_products,
    get_product_by_id,
    get_outlet_menu,
    get_outlet_product_override,
)
from .services import (
    category_create,
    category_update,
    product_create,
    product_update,
    outlet_toggle_product_availability,
    outlet_override_product_price,
)
from .serializers import (
    CategorySerializer,
    CategoryCreateSerializer,
    ProductDetailSerializer,
    ProductCreateUpdateSerializer,
    OutletProductOverrideSerializer,
    OutletStockToggleSerializer,
)
from .pricing_engine import (
    calculate_vat_breakdown,
    calculate_cash_round_down,
    calculate_dynamic_combo_price,
)


class CategoryListCreateAPIView(APIView):
    def get_permissions(self):
        if self.request.method == 'GET':
            return [AllowAny()]
        return [IsAuthenticated()]

    def get(self, request):
        categories = list_categories(include_archived=False)
        serializer = CategorySerializer(categories, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        if not (request.user.is_superuser or request.user.is_staff):
            return Response({"detail": "Permission denied."}, status=status.HTTP_403_FORBIDDEN)

        serializer = CategoryCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        cat = category_create(**serializer.validated_data)
        return Response(CategorySerializer(cat).data, status=status.HTTP_201_CREATED)


class CategoryDetailAPIView(APIView):
    def get_permissions(self):
        if self.request.method == 'GET':
            return [AllowAny()]
        return [IsAuthenticated()]

    def get(self, request, category_id):
        cat = get_category_by_id(category_id)
        if not cat:
            return Response({"detail": "Category not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(CategorySerializer(cat).data, status=status.HTTP_200_OK)

    def patch(self, request, category_id):
        if not (request.user.is_superuser or request.user.is_staff):
            return Response({"detail": "Permission denied."}, status=status.HTTP_403_FORBIDDEN)

        cat = get_category_by_id(category_id)
        if not cat:
            return Response({"detail": "Category not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = CategoryCreateSerializer(cat, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        updated = category_update(cat, **serializer.validated_data)
        return Response(CategorySerializer(updated).data, status=status.HTTP_200_OK)


class ProductListCreateAPIView(APIView):
    def get_permissions(self):
        if self.request.method == 'GET':
            return [AllowAny()]
        return [IsAuthenticated()]

    def get(self, request):
        category_id = request.query_params.get('category')
        channel = request.query_params.get('channel')
        products = list_products(category_id=category_id, channel=channel)
        serializer = ProductDetailSerializer(products, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        if not (request.user.is_superuser or request.user.is_staff):
            return Response({"detail": "Permission denied."}, status=status.HTTP_403_FORBIDDEN)

        serializer = ProductCreateUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        cat = data.pop('category')
        product = product_create(category=cat, **data)
        return Response(ProductDetailSerializer(product).data, status=status.HTTP_201_CREATED)


class ProductDetailAPIView(APIView):
    def get_permissions(self):
        if self.request.method == 'GET':
            return [AllowAny()]
        return [IsAuthenticated()]

    def get(self, request, product_id):
        product = get_product_by_id(product_id)
        if not product:
            return Response({"detail": "Product not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(ProductDetailSerializer(product).data, status=status.HTTP_200_OK)

    def patch(self, request, product_id):
        if not (request.user.is_superuser or request.user.is_staff):
            return Response({"detail": "Permission denied."}, status=status.HTTP_403_FORBIDDEN)

        product = get_product_by_id(product_id)
        if not product:
            return Response({"detail": "Product not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = ProductCreateUpdateSerializer(product, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        updated = product_update(product, **serializer.validated_data)
        return Response(ProductDetailSerializer(updated).data, status=status.HTTP_200_OK)


class OutletMenuAPIView(APIView):
    """
    GET /api/v1/catalog/menu/?outlet_id=<id>&channel=pos|qr|web|delivery|kiosk
    High-Scale Redis Cache-Aside Menu API (Rule 13).
    Serves full catalog with outlet overrides compiled in < 50ms for 10k users.
    """
    permission_classes = [AllowAny]

    def get(self, request):
        outlet_id = request.query_params.get('outlet_id')
        channel = request.query_params.get('channel', 'all')
        force_refresh = request.query_params.get('refresh') == 'true'

        if not outlet_id and request.user.is_authenticated:
            outlet_id = request.user.branch_id
            if not outlet_id and request.user.restaurant:
                first_branch = request.user.restaurant.branches.first()
                if first_branch:
                    outlet_id = first_branch.id

        if not outlet_id:
            # Fallback to first active branch
            branch = Branch.objects.filter(is_active=True).first()
            if branch:
                outlet_id = branch.id

        if not outlet_id:
            return Response({"detail": "No outlet identified."}, status=status.HTTP_404_NOT_FOUND)

        menu_data = get_outlet_menu(
            branch_id=int(outlet_id),
            channel=channel,
            force_refresh=force_refresh,
        )
        return Response(menu_data, status=status.HTTP_200_OK)


class OutletProductToggleStockAPIView(APIView):
    """
    POST /api/v1/catalog/outlets/me/products/<product_id>/toggle-stock/
    Outlet Admin Action:
    Marks an item Available or Out-of-Stock (Sold Out) for their branch.
    Invalidates Redis menu cache and broadcasts real-time WebSocket event
    to POS, Table QR, and Kiosks for ZERO PAGE RELOAD!
    """
    permission_classes = [IsAuthenticated, IsOutletAdminOnly]

    def post(self, request, product_id):
        user = request.user
        branch = None

        if user.branch:
            branch = user.branch
        elif user.restaurant:
            branch = user.restaurant.branches.first()
        elif user.is_superuser:
            branch_id = request.data.get('branch_id')
            branch = Branch.objects.filter(id=branch_id).first() if branch_id else Branch.objects.first()

        if not branch:
            return Response({"detail": "No outlet identified for your account."}, status=status.HTTP_404_NOT_FOUND)

        product = get_product_by_id(product_id)
        if not product:
            return Response({"detail": "Product not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = OutletStockToggleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        is_available = serializer.validated_data['is_available']
        price_override = serializer.validated_data.get('price_override')

        override = outlet_toggle_product_availability(
            branch=branch,
            product=product,
            is_available=is_available,
        )

        if price_override is not None:
            override = outlet_override_product_price(
                branch=branch,
                product=product,
                price_override=price_override,
            )

        return Response({
            "message": f"Product '{product.name}' availability updated to {'Available' if is_available else 'OUT OF STOCK'}.",
            "override": OutletProductOverrideSerializer(override).data,
        }, status=status.HTTP_200_OK)


class PricingCalculationAPIView(APIView):
    """
    POST /api/v1/catalog/calculate-pricing/
    Statutory 13% VAT, Cash Round-Down Savings, and Combo calculation verification endpoint.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        subtotal = request.data.get('subtotal')
        if subtotal is None:
            return Response({"detail": "Subtotal amount is required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            subtotal_dec = Decimal(str(subtotal))
        except Exception:
            return Response({"detail": "Invalid subtotal number."}, status=status.HTTP_400_BAD_REQUEST)

        payment_method = request.data.get('payment_method', 'CASH').upper()
        vat_info = calculate_vat_breakdown(subtotal_dec)

        cash_info = None
        if payment_method == 'CASH':
            cash_info = calculate_cash_round_down(subtotal_dec)

        return Response({
            'vat': vat_info,
            'cash_rounding': cash_info,
            'final_payable_amount': cash_info['final_cash_total'] if cash_info else subtotal_dec,
        }, status=status.HTTP_200_OK)

