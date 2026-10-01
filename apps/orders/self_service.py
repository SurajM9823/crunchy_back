"""Persist kiosk and signed table-QR orders using the same ledger as staff POS."""
import hashlib
import json
import uuid
from decimal import Decimal
from django.core import signing
from django.db import transaction
from rest_framework import serializers
from rest_framework.exceptions import ValidationError, NotFound
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from apps.catalog.serializers import QuoteLineSerializer
from apps.catalog.selectors import quote_items
from apps.restaurants.models import Branch
from apps.tables.models import DiningTable
from apps.tables.qr_security import verify_and_resolve_qr_token
from .models import Order, PosSequence, PosMutation, OrderOutboxEvent
from .preparation import append_allowed, sync_status
from .numbering import generate_order_number
from .pos_services import Conflict, totals, pricing_policy, add_lines, audit, receipt
from .pos_selectors import ACTIVE, order_data, order_queryset


class CheckoutInput(serializers.Serializer):
    branch_id = serializers.IntegerField(min_value=1)
    order_source = serializers.ChoiceField(choices=['KIOSK', 'TABLE_QR'])
    fulfillment_type = serializers.ChoiceField(choices=['DINE_IN', 'TAKEAWAY'])
    table_id = serializers.IntegerField(required=False, allow_null=True)
    qr_token = serializers.CharField(required=False, allow_blank=True, default='')
    tracking_token = serializers.CharField(required=False, allow_blank=True, default='')
    version = serializers.IntegerField(required=False, min_value=1)
    customer_name = serializers.CharField(max_length=120, allow_blank=True, default='')
    customer_phone = serializers.CharField(max_length=32, allow_blank=True, default='')
    notes = serializers.CharField(max_length=1000, allow_blank=True, default='')
    expected_total = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, min_value=0)
    items = QuoteLineSerializer(many=True, allow_empty=False, max_length=100)


@transaction.atomic
def checkout(data, key):
    if not key or len(key) > 128:
        raise ValidationError('An Idempotency-Key is required.')
    branch = Branch.objects.select_for_update().select_related('restaurant').filter(pk=data['branch_id'], is_active=True, restaurant__is_active=True).first()
    if not branch:
        raise NotFound('Outlet not found.')
    fingerprint = hashlib.sha256(json.dumps(['self-service', data], sort_keys=True, default=str).encode()).hexdigest()
    previous = PosMutation.objects.filter(branch=branch, key=key).first()
    if previous:
        if previous.fingerprint != fingerprint:
            raise Conflict('This request key belongs to another order.')
        return previous.response
    if not branch.accepting_orders:
        raise ValidationError('This outlet is not accepting orders.')
    if not getattr(branch, 'enable_kiosk' if data['order_source'] == 'KIOSK' else 'enable_qr_ordering'):
        raise ValidationError('This ordering channel is disabled at this outlet.')
    mode = data['fulfillment_type']
    if not getattr(branch, 'enable_dine_in' if mode == 'DINE_IN' else 'enable_takeaway'):
        raise ValidationError('This dining mode is unavailable.')
    table = None
    if data['order_source'] == 'TABLE_QR':
        table = verify_and_resolve_qr_token(data['qr_token'])
        if not table or table.branch_id != branch.pk:
            raise ValidationError('Scan a valid QR code for this outlet.')
        if mode != 'DINE_IN':
            raise ValidationError('Table QR orders must use dine-in.')
    elif mode == 'DINE_IN':
        table = DiningTable.objects.filter(pk=data.get('table_id'), branch=branch, is_active=True).first()
        if not table:
            raise ValidationError('Select an active table.')
    priced = quote_items(branch, data['items'], 'kiosk' if data['order_source'] == 'KIOSK' else 'qr')
    if data.get('expected_total') != Decimal(totals(branch, priced['subtotal'])['total_payable']):
        raise Conflict('Menu prices changed. Review the current total before submitting again.')
    order = Order.objects.select_for_update().filter(branch=branch, table=table, is_pos_managed=True, status__in=ACTIVE).first() if table else None
    if data.get('tracking_token'):
        try:
            tracked = signing.loads(data['tracking_token'], salt='self-service-order', max_age=7*86400)
        except signing.BadSignature:
            raise ValidationError('Order session expired. Scan the table QR again.')
        if not order or tracked.get('order_id') != order.pk or data.get('version') != order.version:
            raise Conflict('This table order changed or closed. Review the current order before adding items.')
    # A kiosk cannot attach to another party's running tab just by selecting its table.
    if order and data['order_source'] == 'KIOSK':
        raise Conflict('This table has a running order. Scan its QR code to add items.')
    sequence, _ = PosSequence.objects.get_or_create(branch=branch)
    old_status = order.status if order else ''
    if order:
        if not append_allowed(order):
            raise ValidationError('This order cannot accept another round. Ask staff to start a new order.')
        if order.paid_amount or order.billed_at:
            raise Conflict('This table has been billed. Ask staff to start a new tab.')
        order.subtotal += Decimal(priced['subtotal'])
        order.version += 1
    else:
        order = Order.objects.create(branch=branch, is_pos_managed=True, order_source=data['order_source'],
            order_number=generate_order_number(data['order_source']),
            table=table, table_session_id=uuid.uuid4() if table else None, fulfillment_type=mode,
            customer_name=data['customer_name'] or 'Guest', customer_phone=data['customer_phone'],
            notes=data['notes'], payment_method='CASH', pricing_policy=pricing_policy(branch), subtotal=priced['subtotal'])
        if table:
            table.active_session_id = order.table_session_id
            table.save(update_fields=['active_session_id', 'updated_at'])
    add_lines(order, data['items'], priced, None)
    sync_status(order)
    for field, value in totals(branch, order.subtotal, order.discount_amount, order.payment_method, order.pricing_policy).items():
        setattr(order, field, Decimal(value))
    order.save()
    audit(order, None, old_status, 'Self-service additional round' if old_status else 'Self-service order placed; payment due at counter')
    receipt(order, 'TOKEN', sequence)
    OrderOutboxEvent.objects.create(branch=branch, order=order, event_type='ORDER_APPEND' if old_status else 'ORDER_CREATE', payload={'version': order.version})
    from apps.catalog.services import menu_changed
    menu_changed(branch_id=branch.pk)
    result = order_data(order_queryset(branch).get(pk=order.pk))
    result['tracking_token'] = signing.dumps({'order_id': order.pk}, salt='self-service-order')
    PosMutation.objects.create(branch=branch, key=key, fingerprint=fingerprint, response=result)
    return result


class SelfServiceCheckoutView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = CheckoutInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(checkout(serializer.validated_data, request.headers.get('Idempotency-Key')), status=201)


class SelfServiceQuoteView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = CheckoutInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        branch = Branch.objects.select_related('restaurant').filter(pk=data['branch_id'], is_active=True).first()
        if not branch:
            raise NotFound('Outlet not found.')
        quoted = quote_items(branch, data['items'], 'kiosk' if data['order_source'] == 'KIOSK' else 'qr')
        return Response(totals(branch, quoted['subtotal']))


class SelfServiceOrderView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        try:
            data = signing.loads(request.query_params.get('token', ''), salt='self-service-order', max_age=7*86400)
        except signing.BadSignature:
            raise NotFound('Order link is invalid or expired.')
        order = Order.objects.select_related('branch').filter(pk=data['order_id']).first()
        if not order:
            raise NotFound('Order not found.')
        data = order_data(order_queryset(order.branch).get(pk=order.pk))
        data['tracking_token'] = request.query_params['token']
        response = Response(data)
        response['Cache-Control'] = 'private, no-store'
        return response


class SelfServiceTablesView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, outlet_id):
        tables = DiningTable.objects.filter(branch_id=outlet_id, branch__is_active=True, is_active=True)
        return Response({'tables': list(tables.values('id', 'table_number', 'section'))})


class SelfServiceVoidInput(serializers.Serializer):
    tracking_token = serializers.CharField()
    qr_token = serializers.CharField()
    version = serializers.IntegerField(min_value=1)
    item_id = serializers.IntegerField(min_value=1)
    quantity = serializers.IntegerField(min_value=1, required=False)


@transaction.atomic
def remove_table_item(data, key):
    from .pos_services import remove_waiting_item
    if not key or len(key) > 128:
        raise ValidationError('An Idempotency-Key is required.')
    try:
        capability = signing.loads(data['tracking_token'], salt='self-service-order', max_age=7*86400)
    except signing.BadSignature:
        raise NotFound('Order session expired.')
    table = verify_and_resolve_qr_token(data['qr_token'])
    if not table:
        raise NotFound('Scan a valid table QR.')
    branch = Branch.objects.select_for_update().get(pk=table.branch_id)
    fingerprint = hashlib.sha256(json.dumps(['table-void', data], sort_keys=True).encode()).hexdigest()
    previous = PosMutation.objects.filter(branch=branch, key=key).first()
    if previous:
        if previous.fingerprint != fingerprint:
            raise Conflict('This request key belongs to another change.')
        return previous.response
    order = Order.objects.select_for_update().filter(pk=capability.get('order_id'), branch=branch, table=table, is_pos_managed=True).first()
    table.refresh_from_db()
    if not order or not table.active_session_id or table.active_session_id != order.table_session_id:
        raise NotFound('This table session is closed.')
    if order.version != data['version']:
        raise Conflict('The kitchen updated this order. Review its latest state before editing.')
    old_status = order.status
    remove_waiting_item(order, {**data,'reason':'Table guest removed waiting item'})
    order.version += 1
    order.save()
    audit(order, None, old_status, f'Table guest removed {data.get("quantity", "all")} from waiting item {data["item_id"]}')
    sequence, _ = PosSequence.objects.get_or_create(branch=branch)
    receipt(order, 'TOKEN', sequence)
    OrderOutboxEvent.objects.create(branch=branch, order=order, event_type='ORDER_VOID', payload={'version':order.version})
    from apps.catalog.services import menu_changed
    menu_changed(branch_id=branch.pk)
    result = order_data(order_queryset(branch).get(pk=order.pk))
    result['tracking_token'] = data['tracking_token']
    PosMutation.objects.create(branch=branch, key=key, fingerprint=fingerprint, response=result)
    return result


class SelfServiceVoidView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = SelfServiceVoidInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(remove_table_item(serializer.validated_data, request.headers.get('Idempotency-Key')))
