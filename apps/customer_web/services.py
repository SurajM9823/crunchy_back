import hashlib
import json
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import ValidationError
from apps.catalog.models import Product
from apps.orders.pos_services import Conflict
from .models import CustomerCart, CustomerCartMerge, CustomerAddress
from .selectors import cart_data


def lock_customer(user):
    type(user).objects.select_for_update().get(pk=user.pk)


@transaction.atomic
def save_cart(user, branch, data, merge=False):
    lock_customer(user)
    cart, _ = CustomerCart.objects.get_or_create(user=user, branch=branch)
    items = data['items']
    ids = {row['productId'] for row in items}
    ids.update(part['product_id'] for row in items for part in row.get('comboSelections', []))
    if Product.objects.filter(pk__in=ids, category__restaurant_id=branch.restaurant_id).count() != len(ids):
        raise ValidationError('Some cart items do not belong to this outlet.')
    if merge:
        if 'merge_id' not in data:
            raise ValidationError({'merge_id': 'Required for guest cart attachment.'})
        fingerprint = hashlib.sha256(json.dumps(items, sort_keys=True).encode()).hexdigest()
        previous = CustomerCartMerge.objects.filter(cart=cart, key=data['merge_id']).first()
        if previous:
            if previous.fingerprint != fingerprint:
                raise Conflict('This guest cart was already attached with different items.')
            return cart_data(cart)
        existing_ids = {row['cartItemId'] for row in cart.items}
        items = cart.items + [row for row in items if row['cartItemId'] not in existing_ids]
        if len(items) > 100:
            raise ValidationError('Your combined cart exceeds 100 lines. Remove some items first.')
        CustomerCartMerge.objects.create(cart=cart, key=data['merge_id'], fingerprint=fingerprint)
    elif data.get('version') != cart.version:
        raise Conflict('Your cart changed on another device. Sync before saving.')
    if items != cart.items:
        cart.items = items
        cart.version += 1
        cart.save(update_fields=['items', 'version'])
    return cart_data(cart)


@transaction.atomic
def save_address(user, data, address_id=None):
    lock_customer(user)
    rows = CustomerAddress.objects.filter(user=user)
    # Retrying an identical creation after an uncertain response must not duplicate it.
    existing = None if address_id else rows.filter(**{field: data.get(field, '' if field == 'landmark' else None)
        for field in ('label', 'address', 'landmark', 'latitude', 'longitude')}).first()
    address = get_object_or_404(rows, pk=address_id) if address_id else existing or CustomerAddress(user=user)
    if not address.pk and rows.count() >= 20:
        raise ValidationError('You can save up to 20 addresses.')
    for field, value in data.items():
        setattr(address, field, value)
    if not rows.exists():
        address.is_default = True
    if address.is_default:
        rows.exclude(pk=address.pk).update(is_default=False)
    address.save()
    return address


@transaction.atomic
def delete_address(user, address_id):
    lock_customer(user)
    address = get_object_or_404(CustomerAddress, user=user, pk=address_id)
    was_default = address.is_default
    address.delete()
    if was_default:
        first = CustomerAddress.objects.filter(user=user).first()
        if first:
            first.is_default = True
            first.save(update_fields=['is_default'])


def consume_cart(user, branch, line_ids, ordered_items):
    """Called under the checkout transaction and customer lock, once per order."""
    cart = CustomerCart.objects.filter(user=user, branch=branch).first()
    if not cart or not line_ids:
        return
    purchased = dict(zip(line_ids, ordered_items))
    remaining = []
    for item in cart.items:
        bought = purchased.get(item['cartItemId'])
        if bought and bought['product_id'] == item['productId']:
            item = {**item, 'quantity': max(0, item['quantity'] - bought['quantity'])}
            item['lineTotal'] = item['quantity'] * item['unitPrice']
        if item['quantity']:
            remaining.append(item)
    cart.items = remaining
    cart.version += 1
    cart.save(update_fields=['items', 'version'])
