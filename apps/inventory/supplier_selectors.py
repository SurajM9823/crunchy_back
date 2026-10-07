from decimal import Decimal
from django.db.models import Q, Sum, Count
from rest_framework.exceptions import NotFound
from .models import Supplier, SupplierPayment, PurchaseInvoice
from .serializers import SupplierSerializer, PurchaseInvoiceSerializer


def supplier_accounts(branch, search=''):
    rows = Supplier.objects.filter(branch=branch)
    if search:
        rows = rows.filter(Q(name__icontains=search) | Q(phone__icontains=search) | Q(pan_number__icontains=search))
    purchases = {row['supplier_id']: row for row in PurchaseInvoice.objects.filter(branch=branch)
        .values('supplier_id').annotate(total=Sum('total_amount'), paid=Sum('initial_paid_amount'), count=Count('pk'))}
    payments = {row['supplier_id']: row['paid'] for row in SupplierPayment.objects.filter(
        supplier__branch=branch, voided_at__isnull=True).values('supplier_id').annotate(paid=Sum('amount'))}
    result = []
    for supplier in rows.order_by('name', 'pk'):
        bought = purchases.get(supplier.pk, {})
        result.append({**SupplierSerializer(supplier).data,
            'purchase_total': bought.get('total', Decimal('0')),
            'paid_total': bought.get('paid', Decimal('0')) + payments.get(supplier.pk, Decimal('0')),
            'invoice_count': bought.get('count', 0),
            'amount_due': max(Decimal('0'), supplier.credit_balance),
            'advance': max(Decimal('0'), -supplier.credit_balance)})
    return result


def supplier_account(branch, supplier_id):
    supplier = Supplier.objects.filter(branch=branch, pk=supplier_id).first()
    if not supplier:
        raise NotFound('Supplier not found at this outlet.')
    invoices = list(PurchaseInvoice.objects.filter(branch=branch, supplier=supplier).select_related('received_by').prefetch_related('items').order_by('-purchase_date', '-created_at'))
    payments = list(supplier.payments.select_related('recorded_by', 'voided_by').order_by('-date', '-pk'))
    statement = []
    for invoice in invoices:
        statement.append({'id': f'bill-{invoice.pk}', 'date': str(invoice.purchase_date), 'created_at': invoice.created_at.isoformat(),
            'type': 'PURCHASE', 'reference': invoice.invoice_number, 'description': invoice.notes,
            'charge': invoice.total_amount, 'payment': Decimal('0'), 'method': ''})
        if invoice.initial_paid_amount:
            statement.append({'id': f'paid-{invoice.pk}', 'date': str(invoice.purchase_date), 'created_at': invoice.created_at.isoformat(),
                'type': 'BILL_PAYMENT', 'reference': invoice.invoice_number, 'description': 'Paid when purchase was recorded',
                'charge': Decimal('0'), 'payment': invoice.initial_paid_amount, 'method': invoice.payment_method})
    for payment in payments:
        statement.append({'id': f'payment-{payment.pk}', 'date': str(payment.date), 'created_at': payment.created_at.isoformat(),
            'type': 'PAYMENT_VOIDED' if payment.voided_at else 'PAYMENT', 'reference': payment.reference or f'Payment #{payment.pk}',
            'description': payment.void_reason if payment.voided_at else payment.notes,
            'charge': Decimal('0'), 'payment': Decimal('0') if payment.voided_at else payment.amount, 'method': payment.method})
    statement.sort(key=lambda row: (row['date'], row['created_at'], row['id']))
    balance = supplier.opening_balance
    for row in statement:
        balance += row['charge'] - row['payment']
        row['balance'] = balance
    paid_total = sum((row.initial_paid_amount for row in invoices), Decimal('0')) + sum((p.amount for p in payments if not p.voided_at), Decimal('0'))
    return {'supplier': SupplierSerializer(supplier).data,
        'summary': {'purchase_total': sum((row.total_amount for row in invoices), Decimal('0')),
            'paid_total': paid_total, 'balance': balance, 'amount_due': max(Decimal('0'), balance),
            'advance': max(Decimal('0'), -balance), 'opening_balance': supplier.opening_balance},
        'invoices': PurchaseInvoiceSerializer(invoices, many=True).data,
        'payments': [{'id': p.pk, 'date': p.date, 'amount': p.amount, 'method': p.method, 'reference': p.reference,
            'notes': p.notes, 'recorded_by': p.recorded_by.username, 'voided_at': p.voided_at,
            'void_reason': p.void_reason} for p in payments],
        'statement': list(reversed(statement)),
        'items': list(supplier.inventory_items.filter(branch=branch).values('id', 'name', 'sku', 'unit', 'cost_per_unit'))}
