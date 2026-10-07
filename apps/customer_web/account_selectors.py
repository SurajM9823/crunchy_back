from decimal import Decimal
from django.db.models import Case, When, F, Value, DecimalField, OuterRef, Subquery, Sum
from django.db.models.functions import Coalesce, Greatest, Least
from rest_framework.exceptions import NotFound
from apps.orders.models import Order
from apps.payments.models import PaymentTransaction
from apps.orders.pos_access import can_access
from .models import CustomerContact


def financial_orders(branch, contact_id=None):
    money = DecimalField(max_digits=14, decimal_places=2)
    zero = Value(Decimal('0'), output_field=money)
    successful = PaymentTransaction.objects.filter(order_id=OuterRef('pk'), status='SUCCESS').order_by().values('order_id').annotate(total=Sum('amount')).values('total')
    refunded = PaymentTransaction.objects.filter(order_id=OuterRef('pk'), status='REFUNDED').order_by().values('order_id').annotate(total=Sum('amount')).values('total')
    rows = Order.objects.filter(branch=branch)
    if contact_id is not None:
        rows = rows.filter(customer_contact_id=contact_id)
    rows = rows.annotate(account_paid=Greatest(F('paid_amount'), Coalesce(Subquery(successful), zero),
        Case(When(payment_status='PAID', then=F('total_payable')),
             When(is_pos_managed=False, paid_amount=0, payment_status='REFUNDED', then=F('total_payable')), default=zero, output_field=money), output_field=money),
        account_refunded=Greatest(F('refunded_amount'), Coalesce(Subquery(refunded), zero), output_field=money))
    return rows.annotate(account_due=Case(When(status='CANCELLED', then=zero),
        default=Greatest(F('total_payable')-F('account_paid'), zero), output_field=money)).annotate(
        account_credit=Least(F('credit_amount'), F('account_due'), output_field=money))


def customer_account(branch, contact_id, actor):
    contact = CustomerContact.objects.filter(branch=branch, pk=contact_id).select_related('user').first()
    if not contact:
        raise NotFound('Customer not found at this outlet.')
    rows = list(financial_orders(branch, contact_id).prefetch_related('items', 'payments__received_by', 'pos_receipts', 'credit_entries').order_by('-created_at', '-pk'))
    total = sum((row.total_payable for row in rows if row.status != 'CANCELLED'), Decimal('0'))
    due = sum((row.account_due for row in rows), Decimal('0'))
    credit = sum((row.account_credit for row in rows), Decimal('0'))
    payments, credits = [], []
    for order in rows:
        payments.extend({'id': p.pk, 'order_id': order.pk, 'order_number': order.order_number,
            'transaction_id': p.transaction_id, 'amount': str(p.amount), 'method': p.payment_method, 'status': p.status,
            'reference': p.gateway_ref, 'date': p.created_at, 'recorded_by': p.received_by.username if p.received_by else 'Online payment'}
            for p in order.payments.all())
        credits.extend({'id': p.pk, 'order_number': order.order_number, 'amount': str(p.amount), 'reason': p.reason, 'date': p.created_at}
            for p in order.credit_entries.all())
    return {'customer': {'id': contact.pk, 'name': contact.name or 'Guest', 'phone': contact.phone,
        'email': contact.user.email if contact.user else '', 'registered': bool(contact.user_id), 'sources': contact.sources},
        'summary': {'orders': len(rows), 'order_total': str(total), 'received': str(sum((row.account_paid for row in rows), Decimal('0'))),
            'refunded': str(sum((row.account_refunded for row in rows), Decimal('0'))), 'due': str(due), 'credit': str(credit)},
        'can_receive': can_access(actor, branch, 'billing'),
        'methods': [name for name, field in [('CASH','enable_cash'), ('CARD','enable_card'), ('FONEPAY','enable_fonepay'), ('ESEWA','enable_esewa'), ('KHALTI','enable_khalti')] if getattr(branch.restaurant, field)] + ['BANK_TRANSFER'],
        'orders': [{'id': row.pk, 'order_number': row.order_number, 'source': row.order_source, 'status': row.status,
            'date': row.created_at, 'total': str(row.total_payable), 'paid': str(row.account_paid), 'due': str(row.account_due),
            'credit': str(row.account_credit), 'refunded': str(row.account_refunded),
            'items': [{'id': i.pk, 'name': i.product_name, 'quantity': i.quantity, 'unit_price': str(i.unit_price), 'total': str(i.line_total), 'voided': i.is_voided} for i in row.items.all()],
            'receipts': [{'id': r.pk, 'number': r.number, 'kind': r.kind} for r in row.pos_receipts.all()]} for row in rows],
        'payments': sorted(payments, key=lambda p: p['date'], reverse=True),
        'credits': sorted(credits, key=lambda p: p['date'], reverse=True),
        'collections': [{'id': row.pk, 'snapshot': row.snapshot} for row in contact.collections.order_by('-pk')]}
