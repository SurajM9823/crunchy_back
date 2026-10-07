from decimal import Decimal
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from apps.restaurants.models import Branch
from apps.daybook.serializers import today
from .supplier_selectors import supplier_accounts, supplier_account
from .supplier_services import supplier_mutate, update_supplier
from .serializers import SupplierSerializer


def supplier_branch(request):
    user = request.user
    employee = getattr(user, 'employee_profile', None)
    raw = request.query_params.get('outlet_id') or user.branch_id or (employee.assigned_outlet_id if employee else None)
    branch_id = serializers.IntegerField(min_value=1).run_validation(raw)
    branch = get_object_or_404(Branch, pk=branch_id, is_active=True)
    allowed = user.is_active and (user.is_superuser or
        user.role == 'RESTAURANT_OWNER' and user.restaurant_id == branch.restaurant_id or
        user.role == 'BRANCH_MANAGER' and user.branch_id == branch.pk or
        bool(employee and employee.is_active and employee.assigned_outlet_id == branch.pk and
             set(employee.assigned_pages or []) & {'inventory', 'purchases'}))
    if not allowed:
        raise PermissionDenied('Inventory access is required for this outlet.')
    return branch


class SupplierPaymentInput(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal('0.01'))
    expected_balance = serializers.DecimalField(max_digits=12, decimal_places=2)
    date = serializers.DateField(default=today)
    method = serializers.ChoiceField(choices=['CASH', 'FONEPAY', 'BANK_TRANSFER', 'CHEQUE'])
    reference = serializers.CharField(max_length=128, allow_blank=True, default='')
    notes = serializers.CharField(max_length=1000, allow_blank=True, default='')

    def validate_date(self, value):
        if value > today():
            raise serializers.ValidationError('Record payments already made, not future payments.')
        return value


class SupplierEditInput(serializers.Serializer):
    name = serializers.CharField(max_length=150, required=False)
    phone = serializers.CharField(max_length=50, allow_blank=True, required=False)
    pan_number = serializers.CharField(max_length=50, allow_blank=True, required=False)
    email = serializers.EmailField(allow_blank=True, required=False)
    address = serializers.CharField(allow_blank=True, required=False)
    is_active = serializers.BooleanField(required=False)


class SupplierVoidInput(serializers.Serializer):
    reason = serializers.CharField(max_length=500)


class SupplierAccountsView(APIView):
    permission_classes = [IsAuthenticated]

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response['Cache-Control'] = 'private, no-store'
        return response

    def get(self, request):
        branch = supplier_branch(request)
        return Response({'results': supplier_accounts(branch, request.query_params.get('search', '').strip()[:150])})

    def post(self, request):
        from .serializers import SupplierCreateSerializer
        from .services import supplier_create
        branch = supplier_branch(request)
        serializer = SupplierCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(SupplierSerializer(supplier_create(branch, **serializer.validated_data)).data, status=201)


class SupplierAccountView(SupplierAccountsView):
    def get(self, request, supplier_id):
        return Response(supplier_account(supplier_branch(request), supplier_id))

    def patch(self, request, supplier_id):
        branch = supplier_branch(request)
        serializer = SupplierEditInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(SupplierSerializer(update_supplier(branch, supplier_id, serializer.validated_data)).data)

    def post(self, request, supplier_id, payment_id=None):
        branch = supplier_branch(request)
        serializer = (SupplierVoidInput if payment_id else SupplierPaymentInput)(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(supplier_mutate(branch, request.user, request.headers.get('Idempotency-Key'),
            supplier_id, 'void' if payment_id else 'payment', serializer.validated_data, payment_id))
