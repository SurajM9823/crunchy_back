import uuid
from decimal import Decimal
from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from apps.orders.models import Order, OrderStatus, PaymentStatus, OrderStatusHistory
from apps.tables.services import table_close_dining_session
from .models import PaymentTransaction, FiscalInvoice, TransactionStatus


def generate_transaction_id(branch) -> str:
    now = timezone.now()
    date_str = now.strftime('%y%m%d')
    suffix = uuid.uuid4().hex[:6].upper()
    return f"TXN-{branch.branch_code}-{date_str}-{suffix}"


def generate_invoice_number(branch) -> str:
    now = timezone.now()
    date_str = now.strftime('%y%m%d')
    count = FiscalInvoice.objects.filter(
        branch=branch,
        created_at__date=now.date(),
    ).count() + 1
    return f"INV-{branch.branch_code}-{date_str}-{count:04d}"


@transaction.atomic
def order_settle_payment(
    order: Order,
    payment_method: str,
    amount: Decimal = None,
    idempotency_key: str = None,
    received_by=None,
    customer_pan: str = "",
    gateway_ref: str = "",
    raw_response: dict = None,
) -> tuple:
    """
    Settles an order atomically and issues official Fiscal Tax Invoice.
    - Idempotency guard: Prevents double charges at high scale.
    - Releases dining table session upon completion.
    - Records immutable audit log.
    - Pushes live WebSocket update to POS cashier and customer.
    """
    # 1. Idempotency Check
    if idempotency_key:
        existing_txn = PaymentTransaction.objects.filter(idempotency_key=idempotency_key).first()
        if existing_txn:
            invoice = FiscalInvoice.objects.filter(order=existing_txn.order).first()
            return existing_txn, invoice

    # Lock order row for atomic settlement
    order = Order.objects.select_for_update().get(id=order.id)

    if order.status == OrderStatus.CANCELLED:
        raise ValidationError("Cannot settle a cancelled order.")

    if order.payment_status == PaymentStatus.PAID:
        # Already settled, retrieve invoice
        existing_txn = order.payments.filter(status=TransactionStatus.SUCCESS).first()
        invoice = getattr(order, 'fiscal_invoice', None)
        return existing_txn, invoice

    settle_amount = amount if amount is not None else order.total_payable
    branch = order.branch
    restaurant = branch.restaurant

    # 2. Record Payment Transaction
    txn = PaymentTransaction.objects.create(
        transaction_id=generate_transaction_id(branch),
        order=order,
        branch=branch,
        amount=settle_amount,
        payment_method=payment_method,
        status=TransactionStatus.SUCCESS,
        gateway_ref=gateway_ref.strip(),
        raw_response=raw_response or {},
        idempotency_key=idempotency_key,
        received_by=received_by,
    )

    # 3. Transition Order Status to COMPLETED & PAID
    old_status = order.status
    order.payment_method = payment_method
    order.payment_status = PaymentStatus.PAID
    order.status = OrderStatus.COMPLETED
    order.save()

    # 4. Release Dining Table Session if Dine-In
    if order.table:
        table_close_dining_session(order.table)

    # 5. Generate Official Statutory Fiscal Invoice
    seller_pan = restaurant.pan_number or "000000000"
    taxable_amount = max(Decimal('0.00'), order.subtotal - order.vat_included_amount)

    invoice = FiscalInvoice.objects.create(
        invoice_number=generate_invoice_number(branch),
        order=order,
        branch=branch,
        restaurant=restaurant,
        seller_pan=seller_pan,
        customer_name=order.customer_name,
        customer_pan=customer_pan.strip(),
        subtotal=order.subtotal,
        taxable_amount=taxable_amount,
        vat_amount=order.vat_included_amount,
        cash_round_down_savings=order.cash_round_down_savings,
        grand_total=order.total_payable,
        payment_method=payment_method,
    )

    # 6. Immutable Audit Timeline
    OrderStatusHistory.objects.create(
        order=order,
        from_status=old_status,
        to_status=OrderStatus.COMPLETED,
        changed_by=received_by,
        notes=f"Order settled via {payment_method}. Invoice: {invoice.invoice_number}",
    )

    # 7. Real-Time WebSocket Broadcast (Zero Page Reload)
    channel_layer = get_channel_layer()
    if channel_layer:
        payload = {
            'type': 'order_event',
            'event': 'PAYMENT_SETTLED',
            'order_id': order.id,
            'order_number': order.order_number,
            'invoice_number': invoice.invoice_number,
            'status': order.status,
            'payment_status': order.payment_status,
            'payment_method': order.payment_method,
            'grand_total': str(invoice.grand_total),
            'timestamp': timezone.now().isoformat(),
        }
        async_to_sync(channel_layer.group_send)(f"outlet_{branch.id}_operations", payload)
        async_to_sync(channel_layer.group_send)(f"order_{order.id}", payload)

    return txn, invoice

