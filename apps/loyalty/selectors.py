from decimal import Decimal
from django.db.models import Sum, Count, Q
from apps.orders.models import Order
from .models import LoyaltyCustomer, LoyaltyProgram, LoyaltyPurchase
from .services import eligible_tier


def dashboard(branch, search='', can_manage=False):
    program = LoyaltyProgram.objects.filter(restaurant_id=branch.restaurant_id).first()
    tiers = program.tiers if program else []
    customers = LoyaltyCustomer.objects.filter(restaurant_id=branch.restaurant_id,
        pk__in=LoyaltyPurchase.objects.filter(order__branch=branch).values('customer_id')).annotate(
        spent=Sum('purchases__amount'), visits=Count('purchases', filter=Q(purchases__amount__gt=0)))
    search = search.strip()[:100]
    if search:
        customers = customers.filter(Q(phone__icontains=search) | Q(name__icontains=search))
    logs = Order.objects.filter(branch=branch, discount_amount__gt=0,
        pricing_policy__loyalty__isnull=False).exclude(pricing_policy__loyalty=None).exclude(status='CANCELLED').order_by('-created_at')[:100]
    return {'enabled': bool(program and program.enabled), 'version': program.version if program else 1,
        'tiers': tiers, 'can_manage': can_manage,
        'customer_count': customers.count(), 'customers': [
            {'phone': c.phone, 'name': c.name, 'total_spent': str(c.spent or Decimal(0)), 'paid_orders': c.visits,
             'tier': eligible_tier(tiers, c.spent or Decimal(0)) if program and program.enabled else None}
            for c in customers.order_by('-spent', 'pk')[:200]],
        'discounts': [{'order_number': o.order_number, 'phone': o.customer_phone, 'source': o.order_source,
            'discount': str(o.discount_amount), 'loyalty': o.pricing_policy['loyalty'],
            'created_at': o.created_at.isoformat()} for o in logs]}
