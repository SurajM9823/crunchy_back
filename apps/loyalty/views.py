from decimal import Decimal
from django.db import transaction
from django.db.models import Sum, Count, Q
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from apps.orders.pos_access import staff_branch, can_access
from apps.orders.pos_views import StaffAPIView
from apps.orders.pos_services import Conflict
from apps.orders.models import Order
from .models import LoyaltyCustomer, LoyaltyProgram
from .serializers import ProgramInput
from .services import eligible_tier


class LoyaltyView(StaffAPIView):
    def get(self, request):
        branch = staff_branch(request)
        program = LoyaltyProgram.objects.filter(restaurant_id=branch.restaurant_id).first()
        tiers = program.tiers if program else []
        customers = LoyaltyCustomer.objects.filter(restaurant_id=branch.restaurant_id).annotate(
            spent=Sum('purchases__amount'), visits=Count('purchases', filter=Q(purchases__amount__gt=0)))
        search = request.query_params.get('search', '').strip()[:100]
        if search:
            customers = customers.filter(Q(phone__icontains=search) | Q(name__icontains=search))
        logs = Order.objects.filter(branch__restaurant_id=branch.restaurant_id,
            pricing_policy__loyalty__isnull=False).exclude(status='CANCELLED').order_by('-created_at')[:100]
        return Response({'enabled': bool(program and program.enabled), 'version': program.version if program else 1,
            'tiers': tiers, 'can_manage': can_access(request.user, branch, 'discount'),
            'customer_count': customers.count(), 'customers': [
                {'phone': c.phone, 'name': c.name, 'total_spent': str(c.spent or Decimal(0)), 'paid_orders': c.visits,
                 'tier': eligible_tier(tiers, c.spent or Decimal(0)) if program and program.enabled else None}
                for c in customers.order_by('-spent', 'pk')[:200]],
            'discounts': [{'order_number': o.order_number, 'phone': o.customer_phone, 'source': o.order_source,
                'discount': str(o.discount_amount), 'loyalty': o.pricing_policy['loyalty'],
                'created_at': o.created_at.isoformat()} for o in logs]})

    @transaction.atomic
    def put(self, request):
        branch = staff_branch(request, 'discount')
        if request.user.role not in ('RESTAURANT_OWNER', 'BRANCH_MANAGER') and not request.user.is_superuser:
            employee = getattr(request.user, 'employee_profile', None)
            if not employee or employee.role not in ('STORE_MANAGER', 'SUPER_ADMIN'):
                raise PermissionDenied('Only managers can configure loyalty.')
        serializer = ProgramInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        from apps.restaurants.models import Restaurant
        Restaurant.objects.select_for_update().get(pk=branch.restaurant_id)
        program, _ = LoyaltyProgram.objects.get_or_create(restaurant_id=branch.restaurant_id)
        values = serializer.validated_data
        if values['version'] != program.version:
            raise Conflict('Loyalty rules changed. Reload them before saving.')
        program.enabled, program.tiers = values['enabled'], values['tiers']
        program.version += 1
        program.save()
        return Response({'enabled': program.enabled, 'tiers': program.tiers, 'version': program.version})
