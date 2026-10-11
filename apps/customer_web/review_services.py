from django.core import signing
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import NotFound, ValidationError
from apps.orders.models import Order
from .models import CustomerOrder, OrderReview


def review_data(order):
    return OrderReview.objects.filter(order=order).values('rating', 'comment').first()


def verified_review_order(data):
    if data.get('token'):
        try:
            order_id = int(signing.Signer(salt='receipt-order-status').unsign(data['token']))
        except (signing.BadSignature, ValueError):
            raise NotFound('This order tracking link is invalid.')
        return get_object_or_404(Order, pk=order_id)
    from .views import normalize_customer_phone
    phone = normalize_customer_phone(data.get('customer_phone'))
    link = CustomerOrder.objects.select_related('order').filter(
        user__isnull=True, order__order_number=data.get('order_number', '')).first()
    if not phone or not link or normalize_customer_phone(link.order.customer_phone) != phone:
        raise NotFound('No guest order matches that order number and phone number.')
    return link.order


@transaction.atomic
def save_review(order, rating, comment):
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.status != 'COMPLETED':
        raise ValidationError('You can review your order once it is completed.')
    # One review per verified order; retries return the original saved review.
    review, _ = OrderReview.objects.get_or_create(order=order, defaults={'rating': rating, 'comment': comment})
    return {'rating': review.rating, 'comment': review.comment}
