import uuid
from decimal import Decimal
from django.db import transaction, models
from django.core.exceptions import ValidationError
from django.utils import timezone
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from apps.restaurants.models import Branch
from apps.tables.models import DiningTable
from apps.tables.services import table_open_dining_session, table_close_dining_session
from apps.catalog.models import Product, ProductVariant, ModifierOption, OutletProductOverride
from apps.catalog.pricing_engine import (
    calculate_line_item_unit_price,
    calculate_vat_breakdown,
    calculate_cash_round_down,
    round_currency,
)
from .models import (
    Order,
    OrderItem,
    OrderItemModifier,
    OrderStatusHistory,
    OrderStatus,
    FulfillmentType,
    OrderSource,
    PaymentMethod,
    PaymentStatus,
)


def generate_order_number(branch: Branch) -> str:
    """
    Generates a clean human-readable unique order code.
    Format: <BRANCH_CODE>-YYMMDD-<INCREMENT>
    Example: CB-KTM-01-260922-0001
    """
    now = timezone.now()
    date_str = now.strftime('%y%m%d')
    prefix = f"{branch.branch_code}-{date_str}-"

    # Count orders created today for this branch
    count_today = Order.objects.filter(
        branch=branch,
        created_at__date=now.date(),
    ).count() + 1

    return f"{prefix}{count_today:04d}"


def broadcast_order_event(order: Order, event_type: str, extra_data: dict = None):
    """
    Dispatches zero-page-reload WebSocket events to KDS, POS, TV, and customer phone.
    """
    channel_layer = get_channel_layer()
    if not channel_layer:
        return

    branch_id = order.branch_id
    payload = {
        'type': 'order_event',
        'event': event_type,
        'order_id': order.id,
        'order_number': order.order_number,
        'status': order.status,
        'fulfillment_type': order.fulfillment_type,
        'table_number': order.table.table_number if order.table else None,
        'total_payable': str(order.total_payable),
        'timestamp': timezone.now().isoformat(),
    }
    if extra_data:
        payload.update(extra_data)

    # 1. Customer live tracking stream
    async_to_sync(channel_layer.group_send)(f"order_{order.id}", payload)

    # 2. Cashier POS & Operations stream
    async_to_sync(channel_layer.group_send)(f"outlet_{branch_id}_operations", payload)

    # 3. Kitchen Display System (KDS) stream
    # Only if order contains items that require kitchen prep
    has_kitchen_items = order.items.filter(requires_kitchen=True).exists()
    if has_kitchen_items:
        kitchen_payload = dict(payload)
        kitchen_payload['type'] = 'kitchen_ticket_update'
        async_to_sync(channel_layer.group_send)(f"outlet_{branch_id}_kitchen", kitchen_payload)

    # 4. TV pickup display stream (when in PREPARING or READY)
    if order.status in (OrderStatus.PREPARING, OrderStatus.READY):
        display_payload = dict(payload)
        display_payload['type'] = 'display_update'
        async_to_sync(channel_layer.group_send)(f"outlet_{branch_id}_display", display_payload)


@transaction.atomic
def order_create_or_append_tab(
    branch: Branch,
    raw_items: list,
    fulfillment_type: str = FulfillmentType.DINE_IN,
    order_source: str = OrderSource.TABLE_QR,
    table: DiningTable = None,
    customer_name: str = "Guest",
    customer_phone: str = "",
    payment_method: str = PaymentMethod.CASH,
    delivery_address: str = "",
    notes: str = "",
    quote_timestamp: int = None,
) -> Order:
    """
    Single Source of Truth Order Creation & Revalidation Service.
    - Prevents price tampering by computing all prices server-side.
    - Validates outlet stock availability.
    - Checks 15-minute quote snapshot validity.
    - Appends rounds to existing table tab if an active unpaid session exists.
    - Computes 13% tax-inclusive VAT and cash round-down savings.
    """
    if not raw_items:
        raise ValidationError("Order must contain at least one line item.")

    # 1. 15-Minute Quote Snapshot Expiry Validation
    if quote_timestamp:
        now_ms = int(timezone.now().timestamp() * 1000)
        fifteen_min_ms = 15 * 60 * 1000
        if now_ms - quote_timestamp > fifteen_min_ms:
            raise ValidationError("Cart quote snapshot has expired. Please refresh the menu.")

    # 2. Check for active running table tab
    existing_active_order = None
    round_number = 1
    table_session_id = None

    if fulfillment_type == FulfillmentType.DINE_IN and table:
        table_session_id = table_open_dining_session(table)
        existing_active_order = (
            Order.objects
            .filter(
                branch=branch,
                table=table,
                table_session_id=table_session_id,
                payment_status=PaymentStatus.UNPAID,
                status__in=[OrderStatus.PENDING, OrderStatus.ACCEPTED, OrderStatus.PREPARING, OrderStatus.READY]
            )
            .first()
        )
        if existing_active_order:
            max_round = existing_active_order.items.aggregate(models.Max('round_number'))['round_number__max'] or 1
            round_number = max_round + 1

    # 3. Server-Side Price Revalidation & Line Item Construction
    # Fetch overrides for this outlet
    product_ids = [item['product_id'] for item in raw_items]
    overrides = {
        ov.product_id: ov
        for ov in OutletProductOverride.objects.filter(branch=branch, product_id__in=product_ids)
    }

    validated_line_items = []
    round_subtotal = Decimal('0.00')

    for raw_item in raw_items:
        product = Product.objects.filter(id=raw_item['product_id']).first()
        if not product:
            raise ValidationError(f"Product {raw_item['product_id']} does not exist.")

        # Check outlet-specific availability
        override = overrides.get(product.id)
        is_available = override.is_available if override else product.is_available
        if not is_available:
            raise ValidationError(f"Item '{product.name}' is currently OUT OF STOCK at this outlet.")

        # Variant resolution
        variant = None
        variant_price = None
        if raw_item.get('variant_id'):
            variant = ProductVariant.objects.filter(id=raw_item['variant_id'], product=product).first()
            if not variant:
                raise ValidationError(f"Variant {raw_item['variant_id']} not valid for {product.name}.")
            variant_price = variant.price

        # Base price or outlet price override
        effective_base_price = (
            override.price_override
            if (override and override.price_override is not None)
            else product.base_price
        )

        # Modifiers resolution
        modifier_options = []
        modifier_deltas = []
        if raw_item.get('modifier_option_ids'):
            modifier_options = list(
                ModifierOption.objects
                .select_related('group')
                .filter(id__in=raw_item['modifier_option_ids'], group__product=product)
            )
            modifier_deltas = [opt.price_delta for opt in modifier_options]

        # Calculate unit price using single source of truth engine
        unit_price = calculate_line_item_unit_price(
            base_price=effective_base_price,
            variant_price=variant_price,
            modifier_price_deltas=modifier_deltas,
            discount_percent=product.discount_percent,
        )

        qty = int(raw_item.get('quantity', 1))
        if qty < 1:
            raise ValidationError("Item quantity must be at least 1.")

        line_total = round_currency(unit_price * qty)
        round_subtotal += line_total

        validated_line_items.append({
            'product': product,
            'variant': variant,
            'unit_price': unit_price,
            'quantity': qty,
            'line_total': line_total,
            'requires_kitchen': product.requires_kitchen,
            'item_notes': raw_item.get('item_notes', ''),
            'modifier_options': modifier_options,
        })

    # 4. Persist or Append to Existing Order
    if existing_active_order:
        order = existing_active_order
        order.subtotal += round_subtotal
    else:
        order = Order(
            order_number=generate_order_number(branch),
            branch=branch,
            table=table,
            table_session_id=table_session_id,
            customer_name=customer_name.strip() or "Guest",
            customer_phone=customer_phone.strip(),
            fulfillment_type=fulfillment_type,
            order_source=order_source,
            status=OrderStatus.PENDING,
            payment_method=payment_method,
            payment_status=PaymentStatus.UNPAID,
            delivery_address=delivery_address.strip(),
            notes=notes.strip(),
            subtotal=round_subtotal,
        )

    # 5. Compute Statutory 13% Tax-Inclusive VAT & Cash Rounding
    vat_data = calculate_vat_breakdown(order.subtotal)
    order.vat_included_amount = vat_data['vat_amount']

    net_payable = max(Decimal('0.00'), order.subtotal - order.discount_amount)
    if order.payment_method == PaymentMethod.CASH:
        cash_data = calculate_cash_round_down(net_payable)
        order.cash_round_down_savings = cash_data['cash_round_down_savings']
        order.total_payable = cash_data['final_cash_total']
    else:
        order.cash_round_down_savings = Decimal('0.00')
        order.total_payable = net_payable

    order.save()

    # 6. Create OrderItem and OrderItemModifier records
    for item_data in validated_line_items:
        order_item = OrderItem.objects.create(
            order=order,
            product=item_data['product'],
            product_name=item_data['product'].name,
            variant=item_data['variant'],
            variant_name=item_data['variant'].name if item_data['variant'] else "",
            unit_price=item_data['unit_price'],
            quantity=item_data['quantity'],
            line_total=item_data['line_total'],
            requires_kitchen=item_data['requires_kitchen'],
            round_number=round_number,
            item_notes=item_data['item_notes'],
        )

        for opt in item_data['modifier_options']:
            OrderItemModifier.objects.create(
                order_item=order_item,
                group_name=opt.group.name,
                option_name=opt.name,
                price_delta=opt.price_delta,
            )

    # 7. Record Status Audit History
    if not existing_active_order:
        OrderStatusHistory.objects.create(
            order=order,
            from_status="",
            to_status=order.status,
            notes=f"Order created via {order.get_order_source_display()}",
        )
    else:
        OrderStatusHistory.objects.create(
            order=order,
            from_status=order.status,
            to_status=order.status,
            notes=f"Appended Round {round_number} to table session tab",
        )

    # 8. Zero-Page-Reload Real-Time Broadcast
    event_name = 'ROUND_APPENDED' if existing_active_order else 'ORDER_CREATED'
    broadcast_order_event(order, event_name, {'round_number': round_number})

    return order


@transaction.atomic
def order_transition_status(
    order: Order,
    to_status: str,
    changed_by=None,
    user=None,
    notes: str = "",
) -> Order:
    """
    Transitions order to new status, records immutable audit history,
    closes table session upon completion, and broadcasts live WebSocket event.
    """
    if changed_by is None and user is not None:
        changed_by = user
    valid_transitions = {
        OrderStatus.PENDING: [OrderStatus.ACCEPTED, OrderStatus.CANCELLED],
        OrderStatus.ACCEPTED: [OrderStatus.PREPARING, OrderStatus.CANCELLED],
        OrderStatus.PREPARING: [OrderStatus.READY, OrderStatus.CANCELLED],
        OrderStatus.READY: [OrderStatus.OUT_FOR_DELIVERY, OrderStatus.COMPLETED, OrderStatus.CANCELLED],
        OrderStatus.OUT_FOR_DELIVERY: [OrderStatus.COMPLETED, OrderStatus.CANCELLED],
        OrderStatus.COMPLETED: [],
        OrderStatus.CANCELLED: [],
    }

    allowed = valid_transitions.get(order.status, [])
    if to_status not in allowed:
        raise ValidationError(f"Illegal status transition from '{order.status}' to '{to_status}'.")

    from_status = order.status
    order.status = to_status

    if to_status == OrderStatus.COMPLETED:
        order.payment_status = PaymentStatus.PAID
        # Free table session if dine-in
        if order.table:
            table_close_dining_session(order.table)

    order.save()

    # Immutable Audit Log
    OrderStatusHistory.objects.create(
        order=order,
        from_status=from_status,
        to_status=to_status,
        changed_by=changed_by,
        notes=notes.strip(),
    )

    # Real-Time WebSocket Notification (Zero Page Reload)
    broadcast_order_event(order, 'STATUS_CHANGED', {'from_status': from_status, 'to_status': to_status})
    return order

