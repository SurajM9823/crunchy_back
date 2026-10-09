import hashlib
import io
import json
import uuid
from decimal import Decimal
from django.core import signing
from django.db import transaction, IntegrityError
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import ValidationError, PermissionDenied
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from apps.catalog.models import Product
from apps.catalog.selectors import quote_items
from apps.orders.models import Order, PosSequence, OrderOutboxEvent
from apps.orders.numbering import generate_order_number
from apps.orders.pos_access import require_access
from apps.orders.pos_selectors import order_data, order_queryset
from apps.orders.pos_serializers import PosLineSerializer
from apps.orders.pos_services import totals, pricing_policy, add_lines, audit, receipt, Conflict, restore_stock
from apps.restaurants.models import Branch
from .models import CustomerProfile, CustomerOrder
from . import selectors, services
from .serializers import AddressSerializer, CartInput


class CustomerView(APIView):
    permission_classes = [IsAuthenticated]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not request.user.is_active:
            raise PermissionDenied('This account is disabled.')

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response['Cache-Control'] = 'private, no-store'
        return response


class ProfileView(CustomerView):
    def get(self, request):
        profile, _ = CustomerProfile.objects.get_or_create(user=request.user)
        return Response({'name': request.user.username, 'phone': request.user.phone_number, 'email': request.user.email or '',
                         'address': profile.address, 'member_since': request.user.created_at.isoformat(),
                         'favorites': list(profile.favorites.values_list('pk', flat=True))})

    def patch(self, request):
        profile, _ = CustomerProfile.objects.get_or_create(user=request.user)
        address = serializers.CharField(max_length=1000, allow_blank=True).run_validation(request.data.get('address', profile.address))
        name = serializers.RegexField(r'^[\w.@+-]+$', min_length=3, max_length=150).run_validation(request.data.get('name', request.user.username))
        email = serializers.EmailField(allow_blank=True).run_validation(request.data.get('email', request.user.email or '')).lower()
        try:
            with transaction.atomic():
                profile.address = address
                profile.save(update_fields=['address'])
                request.user.username = name
                request.user.email = email or None
                request.user.save(update_fields=['username','email'])
        except IntegrityError:
            raise ValidationError('This username or email is already registered.')
        return self.get(request)


class FavoriteView(CustomerView):
    def post(self, request, product_id):
        product = get_object_or_404(Product, pk=product_id)
        profile, _ = CustomerProfile.objects.get_or_create(user=request.user)
        selected = serializers.BooleanField().run_validation(request.data.get('selected'))
        (profile.favorites.add if selected else profile.favorites.remove)(product)
        return Response({'favorites': list(profile.favorites.values_list('pk', flat=True))})


class AddressView(CustomerView):
    def get(self, request):
        return Response(AddressSerializer(selectors.addresses(request.user), many=True).data)

    def post(self, request):
        serializer = AddressSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(AddressSerializer(services.save_address(request.user, serializer.validated_data)).data, status=201)


class AddressDetailView(CustomerView):
    def get(self, request, address_id):
        return Response(AddressSerializer(get_object_or_404(selectors.addresses(request.user), pk=address_id)).data)

    def patch(self, request, address_id):
        address = get_object_or_404(selectors.addresses(request.user), pk=address_id)
        serializer = AddressSerializer(address, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        return Response(AddressSerializer(services.save_address(request.user, serializer.validated_data, address_id)).data)

    def delete(self, request, address_id):
        services.delete_address(request.user, address_id)
        return Response(status=204)


class CartView(CustomerView):
    def get(self, request):
        return Response(selectors.get_cart(request.user, branch_for(request)))

    def put(self, request):
        serializer = CartInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(services.save_cart(request.user, branch_for(request), serializer.validated_data))

    def post(self, request):
        serializer = CartInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(services.save_cart(request.user, branch_for(request), serializer.validated_data, merge=True))


def branch_for(request, data=None):
    outlet_id = serializers.IntegerField(min_value=1).run_validation((data or request.query_params).get('outlet_id'))
    return get_object_or_404(Branch.objects.select_related('restaurant'), pk=outlet_id, is_active=True, restaurant__is_active=True)


class CheckoutMetaView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        branch = branch_for(request)
        qr = branch.restaurant.payment_qr
        return Response({'qr_url': request.build_absolute_uri(qr.url) if qr else None, 'merchant': branch.restaurant.name,
                         'accepting_orders': branch.accepting_orders,
                         'fulfillment_modes': [mode for mode, field in [('DELIVERY','enable_delivery'),('TAKEAWAY','enable_takeaway'),('DRIVE_THRU','enable_drive_thru'),('DINE_IN','enable_dine_in')] if getattr(branch,field)],
                         'tables': list(branch.dining_tables.filter(is_active=True).values('id','table_number'))})


class CheckoutInput(serializers.Serializer):
    from .journey_schema import IdentityInput
    analytics_context = IdentityInput(required=False, allow_null=True)
    outlet_id = serializers.IntegerField(min_value=1)
    items = PosLineSerializer(many=True, allow_empty=False, max_length=100)
    cart_line_ids = serializers.ListField(child=serializers.CharField(max_length=120), max_length=100, required=False, default=list)
    fulfillment_type = serializers.ChoiceField(choices=['DELIVERY','TAKEAWAY','DRIVE_THRU','DINE_IN'])
    customer_name = serializers.CharField(max_length=120)
    delivery_address = serializers.CharField(max_length=1000, allow_blank=True, default='')
    delivery_location = serializers.DictField(required=False, default=dict)
    table_id = serializers.IntegerField(min_value=1, allow_null=True, required=False)
    notes = serializers.CharField(max_length=2000, allow_blank=True, default='')
    tip = serializers.DecimalField(max_digits=8, decimal_places=2, min_value=0, max_value=10000, default=0)
    expected_total = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0, required=False)

    def validate_delivery_location(self, value):
        import math
        if not value:
            return {}
        point = {
            'lat': serializers.FloatField(min_value=-90, max_value=90).run_validation(value.get('lat')),
            'lng': serializers.FloatField(min_value=-180, max_value=180).run_validation(value.get('lng')),
            'landmark': serializers.CharField(max_length=150, allow_blank=True).run_validation(value.get('landmark', '')),
        }
        if not math.isfinite(point['lat']) or not math.isfinite(point['lng']):
            raise serializers.ValidationError('Map coordinates must be finite.')
        return point

    def validate(self, data):
        ids = data.get('cart_line_ids', [])
        if ids and (len(ids) != len(data['items']) or len(ids) != len(set(ids))):
            raise serializers.ValidationError('Cart lines do not match the ordered items.')
        return data


def customer_quote(branch, data, phone=''):
    if not branch.accepting_orders:
        raise ValidationError('This outlet is not accepting orders.')
    mode_field = {'DELIVERY':'enable_delivery','TAKEAWAY':'enable_takeaway','DRIVE_THRU':'enable_drive_thru','DINE_IN':'enable_dine_in'}[data['fulfillment_type']]
    if not getattr(branch, mode_field):
        raise ValidationError('This fulfillment method is unavailable at this outlet.')
    priced = quote_items(branch, data['items'], 'web')
    from apps.orders.pos_services import priced_totals
    result = {**priced, **priced_totals(branch, priced['subtotal'], phone=phone, method='FONEPAY')}
    result['tip'] = str(data['tip'])
    result['total_payable'] = str(Decimal(result['total_payable']) + data['tip'])
    return result


class QuoteView(CustomerView):
    def post(self, request):
        serializer = CheckoutInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        return Response(customer_quote(branch_for(request, data), data, request.user.phone_number))


def customer_order_data(link):
    row = order_data(order_queryset(link.order.branch).get(pk=link.order_id))
    seller = link.order.pos_receipts.order_by('pk').values_list('snapshot', flat=True).first() or {}
    row.update(request_key=link.request_key, reorder_items=link.items_payload, seller=seller.get('seller', {}), payment_review='VERIFIED' if row['settlement'] == 'PAID' else link.payment_review,
               tip=str(link.tip), outlet_name=link.order.branch.name, delivery_location=link.delivery_location)
    return row


class CheckoutView(CustomerView):
    def post(self, request):
        try:
            payload = json.loads(request.data.get('payload', ''))
        except (ValueError, TypeError):
            raise ValidationError('Invalid checkout details.')
        serializer = CheckoutInput(data=payload)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        analytics_context = data.pop('analytics_context', None)
        proof = serializers.ImageField().run_validation(request.FILES.get('receipt'))
        if proof.size > 5*1024*1024 or proof.image.format not in ('PNG','JPEG','WEBP'):
            raise ValidationError('Upload a JPEG, PNG, or WebP receipt smaller than 5 MB.')
        proof.seek(0)
        content = proof.read()
        key = request.headers.get('Idempotency-Key', '')
        if not key or len(key) > 128:
            raise ValidationError('A checkout request key is required.')
        fingerprint = hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()+content).hexdigest()
        with transaction.atomic():
            # Per-customer lock makes retries across outlets safe as well.
            type(request.user).objects.select_for_update().get(pk=request.user.pk)
            old = CustomerOrder.objects.filter(user=request.user, request_key=key).select_related('order__branch').first()
            if old:
                if old.fingerprint != fingerprint:
                    raise Conflict('This request key belongs to a different checkout.')
                return Response(customer_order_data(old))
            branch = branch_for(request, data)
            Branch.objects.select_for_update().get(pk=branch.pk)
            from apps.orders.deletion import deleted_web_key
            from apps.orders.models import PosMutation
            if PosMutation.objects.filter(key=deleted_web_key(request.user.pk, key)).exists():
                raise Conflict('This order was permanently deleted. Start a new checkout.')
            if not branch.restaurant.payment_qr:
                raise ValidationError('The outlet has not configured its payment QR yet.')
            priced = customer_quote(branch, data, request.user.phone_number)
            if data.get('expected_total') != Decimal(priced['total_payable']):
                raise Conflict('The total changed. Review the updated amount before submitting.')
            if data['fulfillment_type'] == 'DELIVERY' and not data['delivery_address'].strip():
                raise ValidationError('Delivery address is required.')
            table = None
            if data['fulfillment_type'] == 'DINE_IN':
                from apps.tables.models import DiningTable
                from apps.orders.pos_selectors import ACTIVE
                table = DiningTable.objects.filter(pk=data.get('table_id'), branch=branch, is_active=True).first()
                if not table or Order.objects.filter(table=table, status__in=ACTIVE).exists():
                    raise ValidationError('Choose an available table or ask the staff about your existing tab.')
            sequence, _ = PosSequence.objects.get_or_create(branch=branch)
            policy = pricing_policy(branch)
            policy['customer_tip'] = str(data['tip'])
            policy['loyalty'] = priced['loyalty']
            policy['manual_discount_amount'] = '0.00'
            delivery_location = data['delivery_location'] if data['fulfillment_type'] == 'DELIVERY' else {}
            delivery_address = data['delivery_address']
            if delivery_location:
                delivery_address += f'\n{delivery_location["landmark"]}\nhttps://maps.google.com/?q={delivery_location["lat"]},{delivery_location["lng"]}'
            order = Order.objects.create(branch=branch, is_pos_managed=True, order_source='WEBSITE', status='PENDING',
                order_number=generate_order_number('WEBSITE'), pricing_policy=policy,
                table=table, table_session_id=uuid.uuid4() if table else None, customer_name=data['customer_name'],
                customer_phone=request.user.phone_number, fulfillment_type=data['fulfillment_type'],
                delivery_address=delivery_address, notes=data['notes'], payment_method='FONEPAY',
                **{field: Decimal(priced[field]) for field in ['subtotal','total_payable','discount_amount','service_charge_amount','vat_included_amount','cash_round_down_savings']})
            if priced['loyalty']:
                order.discount_reason = f"Loyalty: {priced['loyalty']['name']} ({priced['loyalty']['percent']}%)"
                order.save(update_fields=['discount_reason'])
            add_lines(order, data['items'], priced, request.user)
            if table:
                table.active_session_id = order.table_session_id
                table.save(update_fields=['active_session_id'])
            link = CustomerOrder.objects.create(user=request.user, order=order, request_key=key, fingerprint=fingerprint,
                receipt_image=content, receipt_type=proof.content_type, tip=data['tip'], items_payload=data['items'], delivery_location=delivery_location)
            from .journey_services import attach_order
            attach_order(order, analytics_context)
            services.consume_cart(request.user, branch, data['cart_line_ids'], data['items'])
            audit(order, request.user, '', 'Customer QR receipt submitted; payment verification pending')
            receipt(order, 'TOKEN', sequence)
            OrderOutboxEvent.objects.create(branch=branch, order=order, event_type='ORDER_CREATE', payload={'version':order.version})
            from apps.catalog.services import menu_changed
            menu_changed(branch_id=branch.pk)
            profile, _ = CustomerProfile.objects.get_or_create(user=request.user)
            if data['delivery_address']:
                profile.address = data['delivery_address']; profile.save(update_fields=['address'])
            return Response(customer_order_data(link), status=201)


class OrdersView(CustomerView):
    def get(self, request):
        links = CustomerOrder.objects.filter(user=request.user).select_related('order__branch').order_by('-order__created_at')
        return Response({'results': [customer_order_data(link) for link in links]})


class CancelView(CustomerView):
    def post(self, request, order_id):
        with transaction.atomic():
            link = get_object_or_404(CustomerOrder.objects.select_related('order__branch'), user=request.user, order_id=order_id)
            order = Order.objects.select_for_update().get(pk=order_id)
            if order.status == 'CANCELLED':
                return Response(customer_order_data(link))
            if order.status != 'PENDING' or order.paid_amount:
                raise ValidationError('Contact the outlet to cancel an accepted or paid order.')
            for item in order.items.all():
                restore_stock(order, item, request.user)
            order.status = 'CANCELLED'; order.version += 1; order.save()
            if order.table_id:
                from apps.tables.services import table_close_dining_session
                table_close_dining_session(order.table)
            audit(order, request.user, 'PENDING', 'Customer requested cancellation; check receipt for refund if funds arrived')
            OrderOutboxEvent.objects.create(branch=order.branch, order=order, event_type='ORDER_CANCELLED', payload={'version':order.version})
            from apps.catalog.services import menu_changed
            menu_changed(branch_id=order.branch_id)
            return Response(customer_order_data(link))


class PaymentProofView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, order_id):
        link = get_object_or_404(CustomerOrder.objects.select_related('order__branch'), order_id=order_id)
        if link.user_id != request.user.pk:
            require_access(request.user, link.order.branch, 'billing')
        response = FileResponse(io.BytesIO(bytes(link.receipt_image)), content_type=link.receipt_type)
        response['Cache-Control'] = 'private, no-store'
        response['X-Content-Type-Options'] = 'nosniff'
        return response


class SocketTicketView(CustomerView):
    def post(self, request):
        return Response({'ticket': signing.dumps({'user_id':request.user.pk}, salt='customer-socket'), 'path':'/ws/customer/orders/'})
