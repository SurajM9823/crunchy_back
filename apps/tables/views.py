from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.core.exceptions import ValidationError

from apps.restaurants.permissions import IsOutletAdminOrStaff, IsOutletAdminOnly
from apps.restaurants.models import Branch
from .models import DiningTable
from .selectors import list_tables_by_branch, get_table_by_id
from .services import table_create, table_update, table_regenerate_qr_salt
from .serializers import (
    DiningTableSerializer,
    DiningTableCreateUpdateSerializer,
    TableQRResolvedContextSerializer,
)
from .qr_security import verify_and_resolve_qr_token


class DiningTableListCreateAPIView(APIView):
    """
    GET /api/v1/tables/ -> List all dining tables for the authenticated outlet.
    POST /api/v1/tables/ -> Create a new dining table in this branch.
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

        tables = list_tables_by_branch(branch.id)
        serializer = DiningTableSerializer(tables, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        branch = self._get_branch(request)
        if not branch:
            return Response({"detail": "No outlet identified."}, status=status.HTTP_404_NOT_FOUND)

        serializer = DiningTableCreateUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        table = table_create(
            branch=branch,
            **serializer.validated_data
        )
        return Response(DiningTableSerializer(table).data, status=status.HTTP_201_CREATED)


class DiningTableDetailAPIView(APIView):
    """
    GET /api/v1/tables/<int:table_id>/ -> Retrieve table details with printable QR token.
    PATCH /api/v1/tables/<int:table_id>/ -> Update table configuration.
    """
    permission_classes = [IsAuthenticated, IsOutletAdminOrStaff]

    def get(self, request, table_id):
        table = get_table_by_id(table_id)
        if not table:
            return Response({"detail": "Dining table not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(DiningTableSerializer(table).data, status=status.HTTP_200_OK)

    def patch(self, request, table_id):
        table = get_table_by_id(table_id)
        if not table:
            return Response({"detail": "Dining table not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = DiningTableCreateUpdateSerializer(table, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        updated = table_update(table, **serializer.validated_data)
        return Response(DiningTableSerializer(updated).data, status=status.HTTP_200_OK)


class DiningTableRegenerateQRAPIView(APIView):
    """
    POST /api/v1/tables/<int:table_id>/regenerate-qr/
    Revokes the existing QR code token and creates a fresh cryptographic salt.
    """
    permission_classes = [IsAuthenticated, IsOutletAdminOnly]

    def post(self, request, table_id):
        table = get_table_by_id(table_id)
        if not table:
            return Response({"detail": "Dining table not found."}, status=status.HTTP_404_NOT_FOUND)

        updated = table_regenerate_qr_salt(table)
        return Response({
            "message": f"QR Code for table {updated.table_number} regenerated successfully.",
            "table": DiningTableSerializer(updated).data,
        }, status=status.HTTP_200_OK)


class TableQRResolveAPIView(APIView):
    """
    GET /api/v1/tables/qr/resolve/?token=<token>
    Stateless Opaque QR Token Resolver:
    Publicly accessed by customer mobile cameras/browsers.
    Validates HMAC signature and returns verified branch and table context in sub-5ms.
    """
    permission_classes = [AllowAny]

    def get(self, request):
        token = request.query_params.get('token')
        if not token:
            return Response({"detail": "QR token is required."}, status=status.HTTP_400_BAD_REQUEST)

        table = verify_and_resolve_qr_token(token)
        if not table:
            return Response(
                {"detail": "Invalid or expired Table QR code. Please scan the QR code on your table again."},
                status=status.HTTP_400_BAD_REQUEST
            )

        serializer = TableQRResolvedContextSerializer(table)
        return Response(serializer.data, status=status.HTTP_200_OK)

