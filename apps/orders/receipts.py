"""Saved receipt documents and narrowly scoped public pickup-status links."""
import base64
import io
from urllib.parse import urlencode
from django.conf import settings
from django.core import signing
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import Order, PosReceipt


def tracking_token(order_id):
    # Stable, unguessable receipt capability; grants status access only.
    return signing.Signer(salt='receipt-order-status').sign(str(order_id))


def receipt_document(row, request=None):
    import qrcode
    from qrcode.image.svg import SvgPathImage
    origin = settings.FRONTEND_BASE_URL.rstrip('/')
    url = f'{origin}/track?{urlencode({"token": tracking_token(row.order_id)})}'
    def qr_image(value):
        output = io.BytesIO()
        qrcode.make(value, image_factory=SvgPathImage, box_size=6, border=4).save(output)
        return 'data:image/svg+xml;base64,' + base64.b64encode(output.getvalue()).decode()
    seller = dict(row.snapshot.get('seller', {}))
    branch = row.order.branch
    restaurant = branch.restaurant
    payment_qr = restaurant.payment_qr.url if restaurant.payment_qr else None
    if payment_qr and request is not None:
        payment_qr = request.build_absolute_uri(payment_qr)
    defaults = {'address': branch.address_line or restaurant.address or branch.city,
                'phone': branch.phone_number or restaurant.phone,
                'logo': restaurant.logo.url if restaurant.logo else restaurant.logo_url,
                'website': restaurant.website or origin}
    for key, value in defaults.items():
        if not seller.get(key):
            seller[key] = value
    website = seller['website']
    return {'number': row.number, 'kind': row.kind, 'created_at': row.created_at.isoformat(),
            'snapshot': {**row.snapshot, 'seller': seller}, 'tracking_url': url,
            'tracking_qr': qr_image(url), 'payment_qr': payment_qr,
            'website_url': website, 'website_qr': qr_image(website)}


def latest_receipt(order_id):
    row = PosReceipt.objects.filter(order_id=order_id, kind__in=['TOKEN', 'BILL']).order_by('-created_at', '-pk').first()
    if row is None:
        raise NotFound('A saved receipt is not available for this order.')
    return row


class CustomerOrderSlipView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, order_id):
        from apps.customer_web.models import CustomerOrder
        get_object_or_404(CustomerOrder, order_id=order_id, user=request.user)
        response = Response(receipt_document(latest_receipt(order_id), request))
        response['Cache-Control'] = 'private, no-store'
        return response


class SelfServiceReceiptView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        try:
            data = signing.loads(request.query_params.get('token', ''), salt='self-service-order', max_age=7*86400)
        except signing.BadSignature:
            raise NotFound('Order link is invalid or expired.')
        response = Response(receipt_document(latest_receipt(data['order_id']), request))
        response['Cache-Control'] = 'private, no-store'
        return response


class ReceiptTrackingView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        try:
            order_id = int(signing.Signer(salt='receipt-order-status').unsign(request.query_params.get('token', '')))
        except (signing.BadSignature, ValueError):
            raise NotFound('This order tracking link is invalid.')
        order = get_object_or_404(Order.objects.select_related('branch', 'table'), pk=order_id)
        from .preparation import rounds
        response = Response({'order_number': order.order_number, 'outlet_id': order.branch_id,
            'rounds': [{key:value for key,value in row.items() if key != 'item_ids'} for row in rounds(order)],
            'outlet_name': order.branch.name, 'status': order.status, 'fulfillment_type': order.fulfillment_type,
            'table_number': order.table.table_number if order.table_id else None,
            'updated_at': order.updated_at.isoformat(),
            'history': [{'status': row.to_status, 'timestamp': row.created_at.isoformat()}
                        for row in order.status_history.order_by('created_at', 'pk')]})
        response['Cache-Control'] = 'no-store'
        return response
