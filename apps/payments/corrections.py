from django.db import transaction
from rest_framework import serializers
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from apps.orders.pos_views import StaffAPIView
from apps.orders.pos_access import staff_branch, require_access
from apps.orders.pos_services import Conflict
from apps.orders.pos_selectors import order_data, order_queryset
from apps.orders.models import Order, OrderOutboxEvent, PosReceipt
from apps.restaurants.models import Branch
from apps.daybook.models import DaybookEntry, DaybookEvent
from apps.daybook.serializers import METHODS
from apps.inventory.models import PurchaseInvoice, SupplierPayment
from apps.inventory.supplier_views import supplier_branch
from .models import PaymentTransaction, FiscalInvoice, PaymentMethodCorrection


class CorrectionInput(serializers.Serializer):
    kind = serializers.ChoiceField(choices=['payment', 'purchase', 'daybook'])
    target = serializers.CharField(max_length=128)
    expected_method = serializers.CharField(max_length=32)
    method = serializers.ChoiceField(choices=METHODS)
    reason = serializers.CharField(max_length=500)


@transaction.atomic
def correct_method(branch, actor, data):
    Branch.objects.select_for_update().get(pk=branch.pk)
    kind, target, method = data['kind'], data['target'], data['method']
    payment, entry, purchase = None, None, None
    if kind == 'payment':
        payment = PaymentTransaction.objects.filter(branch=branch, transaction_id=target, order__branch=branch).first()
        if not payment:
            raise NotFound('Payment not found at this outlet.')
        old = payment.payment_method
    elif kind == 'purchase':
        purchase = PurchaseInvoice.objects.filter(branch=branch, pk=target).first()
        if not purchase:
            raise NotFound('Purchase not found at this outlet.')
        old = purchase.payment_method
        if method not in ['CASH', 'FONEPAY', 'BANK_TRANSFER', 'CHEQUE']:
            raise ValidationError('Choose a supported purchase payment method.')
    else:
        if not target.isdigit():
            raise ValidationError('Invalid daybook entry.')
        entry = DaybookEntry.objects.filter(branch=branch, pk=target).first()
        if not entry:
            raise NotFound('Daybook entry not found at this outlet.')
        if entry.voided_at:
            raise ValidationError('Voided entries cannot be edited.')
        old = entry.payment_method
        if entry.payment_id:
            payment = PaymentTransaction.objects.filter(pk=entry.payment_id, branch=branch, order__branch=branch).first()
            if not payment:
                raise ValidationError('Linked payment is unavailable.')
    if payment:
        require_access(actor, branch, 'billing')
    if old == 'CREDIT':
        raise ValidationError('Credit is an unpaid balance. Record a collection to settle it rather than changing its method.')
    if payment and payment.status not in ['SUCCESS', 'REFUNDED']:
        raise ValidationError('Only recorded payments or refunds can be corrected.')
    if old != data['expected_method'] and old != method:
        raise Conflict('Payment method changed elsewhere. Refresh the entry before editing.')
    order = None
    if old != method:
        if payment:
            payment.payment_method = method
            payment.save(update_fields=['payment_method', 'updated_at'])
            DaybookEntry.objects.filter(payment=payment, branch=branch).update(payment_method=method)
            # Customer collections retain their own payment method for statements.
            from apps.customer_web.models import CustomerCollection
            collection_id = payment.raw_response.get('customer_collection_id')
            collection = CustomerCollection.objects.filter(pk=collection_id, contact__branch=branch).first() if collection_id else None
            if collection:
                methods = set(PaymentTransaction.objects.filter(branch=branch, raw_response__customer_collection_id=collection_id).values_list('payment_method', flat=True))
                collection.method = next(iter(methods)) if len(methods)==1 else 'SPLIT'
                collection.snapshot = {**collection.snapshot, 'method':collection.method}
                collection.save(update_fields=['method', 'snapshot'])
                DaybookEvent.objects.create(branch=branch, event_type='CUSTOMER_ACCOUNT_UPDATED')
            order = Order.objects.select_for_update().get(pk=payment.order_id, branch=branch)
            methods = set(order.payments.filter(status='SUCCESS').values_list('payment_method', flat=True))
            if order.credit_amount:
                methods.add('CREDIT')
            if methods:
                order.payment_method = next(iter(methods)) if len(methods)==1 else 'SPLIT'
            order.version += 1
            order.save(update_fields=['payment_method', 'version', 'updated_at'])
            FiscalInvoice.objects.filter(order=order).update(payment_method=order.payment_method)
            for receipt in PosReceipt.objects.filter(order=order):
                snapshot = receipt.snapshot
                for tender in snapshot.get('payments', []):
                    if tender.get('id') == payment.transaction_id:
                        tender['method'] = method
                snapshot_methods = {p['method'] for p in snapshot.get('payments', []) if p.get('status')=='SUCCESS'}
                if float(snapshot.get('credit_amount') or 0)>0:
                    snapshot_methods.add('CREDIT')
                if snapshot_methods:
                    snapshot['payment_method'] = next(iter(snapshot_methods)) if len(snapshot_methods)==1 else 'SPLIT'
                receipt.snapshot = snapshot
                receipt.save(update_fields=['snapshot'])
            OrderOutboxEvent.objects.create(branch=branch, order=order, event_type='ORDER_PAYMENT_METHOD', payload={'version':order.version})
        elif purchase:
            purchase.payment_method = method
            purchase.save(update_fields=['payment_method', 'updated_at'])
        else:
            entry.payment_method = method
            entry.save(update_fields=['payment_method'])
            SupplierPayment.objects.filter(daybook_entry=entry).update(method=method)
        PaymentMethodCorrection.objects.create(branch=branch, kind=kind, target=target,
            previous_method=old, method=method, reason=data['reason'], actor=actor)
        DaybookEvent.objects.create(branch=branch, event_type='SUPPLIER_ACCOUNT_UPDATED' if purchase or (entry and entry.source=='SUPPLIER') else 'DAYBOOK_METHOD_UPDATED')
    if payment and not order:
        order = Order.objects.get(pk=payment.order_id)
    return {'payment_method':method, 'order':order_data(order_queryset(branch).get(pk=order.pk)) if order and order.is_pos_managed else None}


class PaymentMethodView(StaffAPIView):
    def post(self, request):
        serializer = CorrectionInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        branch = supplier_branch(request) if data['kind']=='purchase' else staff_branch(request, 'billing' if data['kind']=='payment' else 'daybook')
        return Response(correct_method(branch, request.user, data))
