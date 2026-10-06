from django.shortcuts import get_object_or_404
from django.utils.http import parse_etags
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.pagination import LimitOffsetPagination
from .access import resolve_branch
from . import selectors, services
from .models import Category, Product, OutletTimePricingSchedule
from .serializers import (CategorySerializer, CategoryCreateSerializer, ProductDetailSerializer,
    ProductCreateUpdateSerializer, OutletStockToggleSerializer, OutletProductOverrideSerializer,
    ScheduleSerializer, QuoteSerializer, PricingSummarySerializer)
from .pricing_engine import calculate_vat_breakdown, calculate_cash_round_down


class CatalogPagination(LimitOffsetPagination):
    default_limit = 50
    max_limit = 200


class ManagementAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def branch(self, request):
        return resolve_branch(request, manage=True)


class CategoryListCreateAPIView(ManagementAPIView):
    def get(self, request):
        branch = self.branch(request)
        return Response(CategorySerializer(selectors.list_categories(True, branch.restaurant_id), many=True).data)

    def post(self, request):
        branch = self.branch(request)
        serializer = CategoryCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        if data.get('id'):
            from rest_framework.exceptions import ValidationError
            if Category.objects.filter(pk=data['id']).exists():
                raise ValidationError({'id': 'Category ID already exists.'})
        obj = services.category_create(restaurant=branch.restaurant, **data)
        return Response(CategorySerializer(obj).data, status=201)


class CategoryDetailAPIView(ManagementAPIView):
    def patch(self, request, category_id):
        branch = self.branch(request)
        obj = get_object_or_404(Category, pk=category_id, restaurant_id=branch.restaurant_id)
        serializer = CategoryCreateSerializer(obj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        return Response(CategorySerializer(services.category_update(obj, **serializer.validated_data)).data)

    def delete(self, request, category_id):
        branch = self.branch(request)
        obj = get_object_or_404(Category, pk=category_id, restaurant_id=branch.restaurant_id)
        services.category_update(obj, is_archived=True)
        return Response(status=204)


class ProductListCreateAPIView(ManagementAPIView):
    def get(self, request):
        branch = self.branch(request)
        qs = selectors.list_products(active_only=False, restaurant_id=branch.restaurant_id,
                                     category_id=request.query_params.get('category'))
        if request.query_params.get('search'):
            qs = qs.filter(name__icontains=request.query_params['search'][:100])
        paginator = CatalogPagination()
        page = paginator.paginate_queryset(qs, request)
        return paginator.get_paginated_response(ProductDetailSerializer(page, many=True, context={'branch': branch}).data)

    def post(self, request):
        branch = self.branch(request)
        serializer = ProductCreateUpdateSerializer(data=request.data, context={'branch': branch})
        serializer.is_valid(raise_exception=True)
        obj = services.product_create(branch=branch, **serializer.validated_data)
        return Response(ProductDetailSerializer(obj, context={'branch': branch}).data, status=201)


class ProductDetailAPIView(ManagementAPIView):
    def get(self, request, product_id):
        branch = self.branch(request)
        obj = get_object_or_404(selectors.product_queryset(), pk=product_id, category__restaurant_id=branch.restaurant_id, is_archived=False)
        return Response(ProductDetailSerializer(obj, context={'branch': branch}).data)

    def patch(self, request, product_id):
        branch = self.branch(request)
        obj = get_object_or_404(Product, pk=product_id, category__restaurant_id=branch.restaurant_id, is_archived=False)
        serializer = ProductCreateUpdateSerializer(obj, data=request.data, partial=True, context={'branch': branch})
        serializer.is_valid(raise_exception=True)
        obj = services.product_update(obj, branch=branch, **serializer.validated_data)
        return Response(ProductDetailSerializer(obj, context={'branch': branch}).data)

    def delete(self, request, product_id):
        branch = self.branch(request)
        obj = get_object_or_404(Product, pk=product_id, category__restaurant_id=branch.restaurant_id, is_archived=False)
        services.product_archive(obj)
        return Response(status=204)


class OutletMenuAPIView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        channel = request.query_params.get('channel', 'web')
        branch = resolve_branch(request, manage=channel == 'all', operational=channel == 'pos')
        data = selectors.get_outlet_menu(branch.pk, channel)
        etag = f'W/"menu-v3-{branch.pk}-{channel}-{data["revision"]}-{data["valid_until"]}"'
        matches = parse_etags(request.headers.get('If-None-Match', ''))
        response = Response(status=304) if etag in matches or '*' in matches else Response(data)
        response['ETag'] = etag
        response['Cache-Control'] = 'private, no-cache' if channel in ('all', 'pos') else 'public, no-cache, must-revalidate'
        return response


class ManagementSnapshotAPIView(ManagementAPIView):
    def get(self, request):
        response = Response(selectors.management_snapshot(self.branch(request)))
        response['Cache-Control'] = 'private, no-store'
        return response


class MenuImageAPIView(ManagementAPIView):
    def post(self, request):
        from .media import store_menu_image
        branch = self.branch(request)
        url = store_menu_image(request.FILES.get('image'), branch.restaurant_id)
        return Response({'url': request.build_absolute_uri(url)}, status=201)


class OutletProductToggleStockAPIView(ManagementAPIView):
    def post(self, request, product_id):
        branch = self.branch(request)
        obj = get_object_or_404(Product, pk=product_id, category__restaurant_id=branch.restaurant_id, is_archived=False)
        serializer = OutletStockToggleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        override = services.outlet_update_product(branch, obj, **serializer.validated_data)
        label = 'Available' if override.is_available else 'OUT OF STOCK'
        return Response({'message': f'Product availability: {label}', 'override': OutletProductOverrideSerializer(override).data})


class ScheduleListAPIView(ManagementAPIView):
    def get(self, request):
        return Response(ScheduleSerializer(OutletTimePricingSchedule.objects.filter(branch=self.branch(request)).order_by('pk'), many=True).data)

    def post(self, request):
        branch = self.branch(request)
        serializer = ScheduleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(ScheduleSerializer(services.schedule_save(branch, serializer.validated_data)).data, status=201)


class ScheduleDetailAPIView(ManagementAPIView):
    def patch(self, request, schedule_id):
        branch = self.branch(request)
        obj = get_object_or_404(OutletTimePricingSchedule, pk=schedule_id, branch=branch)
        serializer = ScheduleSerializer(obj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        return Response(ScheduleSerializer(services.schedule_save(branch, serializer.validated_data, obj)).data)

    def delete(self, request, schedule_id):
        branch = self.branch(request)
        services.schedule_delete(get_object_or_404(OutletTimePricingSchedule, pk=schedule_id, branch=branch))
        return Response(status=204)


class QuoteAPIView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = QuoteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        branch = resolve_branch(request, operational=data['channel'] == 'pos')
        response = Response(selectors.quote_items(branch, data['items'], data['channel']))
        response['Cache-Control'] = 'no-store'
        return response


class PricingCalculationAPIView(APIView):
    """Arithmetic preview only. Checkout uses the authoritative product quote engine."""
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = PricingSummarySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        subtotal = data['subtotal']
        cash = calculate_cash_round_down(subtotal) if data['payment_method'] == 'CASH' else None
        return Response({'vat': calculate_vat_breakdown(subtotal), 'cash_rounding': cash,
                         'final_payable_amount': cash['final_cash_total'] if cash else subtotal})
