from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.core.exceptions import ValidationError

from apps.restaurants.models import Branch
from apps.restaurants.permissions import IsOutletAdminOrStaff
from apps.tables.qr_security import verify_and_resolve_qr_token
from apps.tables.models import DiningTable
from .models import Order, OrderStatus
from .selectors import (
    get_order_by_id,
    get_order_by_number,
    list_orders_for_branch,
    get_kitchen_active_tickets,
    get_live_tv_pickup_tickets,
)
from .services import order_create_or_append_tab, order_transition_status
from .serializers import (
    OrderDetailSerializer,
    CheckoutRequestSerializer,
    OrderStatusTransitionSerializer,
)


class CheckoutAPIView(APIView):
    """
    POST /api/v1/orders/checkout/
    Unified Checkout Endpoint across all channels (Table QR, POS, Kiosk, Web).
    Revalidates all item prices server-side, applies statutory 13% tax-inclusive VAT,
    cash round-down savings, and appends to running table tabs.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = CheckoutRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        branch = None
        table = None

        # 1. Resolve Table & Branch via Cryptographic QR Token
        qr_token = data.get('qr_token')
        if qr_token:
            table = verify_and_resolve_qr_token(qr_token)
            if not table:
                return Response(
                    {"detail": "Invalid or expired Table QR code."},
                    status=status.HTTP_400_BAD_REQUEST
                )
            branch = table.branch

        # 2. Resolve via Table ID
        elif data.get('table_id'):
            table = DiningTable.objects.filter(id=data['table_id'], is_active=True).first()
            if table:
                branch = table.branch

        # 3. Resolve via Branch ID or Authenticated User
        if not branch:
            branch_id = data.get('branch_id')
            if not branch_id and request.user.is_authenticated:
                branch_id = request.user.branch_id

            if branch_id:
                branch = Branch.objects.filter(id=branch_id, is_active=True).first()

        if not branch:
            # Fallback to first active branch for testing
            branch = Branch.objects.filter(is_active=True).first()

        if not branch:
            return Response({"detail": "Unable to identify active outlet."}, status=status.HTTP_400_BAD_REQUEST)

        # Check outlet operational toggle
        if not branch.accepting_orders:
            return Response(
                {"detail": f"{branch.name} is currently not accepting new orders (Kitchen Paused)."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE
            )

        try:
            order = order_create_or_append_tab(
                branch=branch,
                raw_items=data['items'],
                fulfillment_type=data.get('fulfillment_type', 'DINE_IN'),
                order_source=data.get('order_source', 'TABLE_QR'),
                table=table,
                customer_name=data.get('customer_name', 'Guest'),
                customer_phone=data.get('customer_phone', ''),
                payment_method=data.get('payment_method', 'CASH'),
                delivery_address=data.get('delivery_address', ''),
                notes=data.get('notes', ''),
                quote_timestamp=data.get('quote_timestamp'),
            )
            return Response(OrderDetailSerializer(order).data, status=status.HTTP_201_CREATED)
        except ValidationError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class OrderDetailAPIView(APIView):
    """
    GET /api/v1/orders/<id_or_number>/
    Retrieve full order details, line items, and audit history.
    """
    permission_classes = [AllowAny]

    def get(self, request, identifier):
        if str(identifier).isdigit():
            order = get_order_by_id(int(identifier))
        else:
            order = get_order_by_number(str(identifier))

        if not order:
            return Response({"detail": "Order not found."}, status=status.HTTP_404_NOT_FOUND)

        return Response(OrderDetailSerializer(order).data, status=status.HTTP_200_OK)


class OutletOrderListAPIView(APIView):
    """
    GET /api/v1/orders/outlet/me/
    List live orders for the current Outlet Admin's assigned branch.
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

        order_status = request.query_params.get('status')
        fulfillment = request.query_params.get('fulfillment_type')

        orders = list_orders_for_branch(branch_id=branch_id, status=order_status, fulfillment_type=fulfillment)
        return Response(OrderDetailSerializer(orders, many=True).data, status=status.HTTP_200_OK)


class KitchenTicketsAPIView(APIView):
    """
    GET /api/v1/orders/kitchen/me/
    KDS API (Rule 2 KDS Separation):
    Returns active preparation tickets containing strictly items that require kitchen prep.
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

        tickets = get_kitchen_active_tickets(branch_id)
        return Response(OrderDetailSerializer(tickets, many=True).data, status=status.HTTP_200_OK)


class OrderStatusTransitionAPIView(APIView):
    """
    POST /api/v1/orders/<int:order_id>/transition/
    Transition order state (e.g. ACCEPTED -> PREPARING -> READY -> COMPLETED).
    Bypasses page reload and broadcasts live WebSocket update to KDS, TV, and customer.
    """
    permission_classes = [IsAuthenticated, IsOutletAdminOrStaff]

    def post(self, request, order_id):
        order = get_order_by_id(order_id)
        if not order:
            return Response({"detail": "Order not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = OrderStatusTransitionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            updated_order = order_transition_status(
                order=order,
                to_status=serializer.validated_data['to_status'],
                changed_by=request.user,
                notes=serializer.validated_data.get('notes', ''),
            )
            return Response(OrderDetailSerializer(updated_order).data, status=status.HTTP_200_OK)
        except ValidationError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class LiveDisplayAPIView(APIView):
    """
    GET /api/v1/orders/display/<int:outlet_id>/
    Live TV screen endpoint displaying Preparing vs Ready order tickets.
    """
    permission_classes = [AllowAny]

    def get(self, request, outlet_id):
        tickets = get_live_tv_pickup_tickets(outlet_id)
        return Response(tickets, status=status.HTTP_200_OK)

