from django.db.models import Prefetch
from .models import Order, OrderItem, OrderItemModifier, OrderStatus


def get_order_by_id(order_id: int) -> Order:
    return (
        Order.objects
        .select_related('branch', 'branch__restaurant', 'table')
        .prefetch_related(
            Prefetch(
                'items',
                queryset=OrderItem.objects.select_related('product', 'variant').prefetch_related('modifiers')
            ),
            'status_history',
        )
        .filter(id=order_id)
        .first()
    )


def get_order_by_number(order_number: str) -> Order:
    return (
        Order.objects
        .select_related('branch', 'branch__restaurant', 'table')
        .prefetch_related(
            Prefetch(
                'items',
                queryset=OrderItem.objects.select_related('product', 'variant').prefetch_related('modifiers')
            ),
            'status_history',
        )
        .filter(order_number=order_number.strip())
        .first()
    )


def list_orders_for_branch(
    branch_id: int,
    status: str = None,
    fulfillment_type: str = None,
    limit: int = 50,
):
    qs = (
        Order.objects
        .select_related('branch', 'table')
        .prefetch_related(
            Prefetch(
                'items',
                queryset=OrderItem.objects.select_related('product', 'variant').prefetch_related('modifiers')
            )
        )
        .filter(branch_id=branch_id)
    )
    if status:
        qs = qs.filter(status=status)
    if fulfillment_type:
        qs = qs.filter(fulfillment_type=fulfillment_type)

    return qs.order_by('-created_at')[:limit]


def get_kitchen_active_tickets(branch_id: int):
    """
    KDS Active Preparation Tickets (Rule 2 KDS Separation):
    Fetches active kitchen orders and pre-fetches ONLY line items with requires_kitchen=True.
    Non-kitchen items (canned drinks, retail items) are completely filtered out from KDS tickets.
    """
    return (
        Order.objects
        .select_related('table', 'branch')
        .prefetch_related(
            Prefetch(
                'items',
                queryset=OrderItem.objects.filter(requires_kitchen=True).prefetch_related('modifiers')
            )
        )
        .filter(
            branch_id=branch_id,
            status__in=[OrderStatus.PENDING, OrderStatus.ACCEPTED, OrderStatus.PREPARING],
            items__requires_kitchen=True,
        )
        .distinct()
        .order_by('created_at')
    )


def get_live_tv_pickup_tickets(branch_id: int) -> dict:
    """
    TV Screen Live Display Query:
    Returns order numbers separated into 'preparing' and 'ready' columns.
    """
    preparing_orders = list(
        Order.objects
        .filter(branch_id=branch_id, status=OrderStatus.PREPARING)
        .order_by('created_at')
        .values_list('order_number', flat=True)[:20]
    )
    ready_orders = list(
        Order.objects
        .filter(branch_id=branch_id, status=OrderStatus.READY)
        .order_by('-updated_at')
        .values_list('order_number', flat=True)[:20]
    )
    return {
        'preparing': preparing_orders,
        'ready': ready_orders,
    }

