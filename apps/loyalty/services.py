"""Server-authoritative discounts and idempotent, phone-based purchase accounting."""
from decimal import Decimal, ROUND_HALF_UP
from django.db import transaction
from django.db.models import Sum
from apps.customer_web.audience_services import normalized_phone, contact_phone
from .models import LoyaltyCustomer, LoyaltyProgram, LoyaltyPurchase

ZERO = Decimal('0.00')


def eligible_tier(tiers, spent):
    return next((t for t in sorted(tiers, key=lambda t: Decimal(t['threshold']), reverse=True)
                 if spent >= Decimal(t['threshold'])), None)


def offer(branch, phone, subtotal, exclude_order=None):
    phone = contact_phone(phone)
    if not phone:
        return None
    from apps.customer_web.models import CustomerContact
    contact = CustomerContact.objects.filter(branch=branch, phone=phone, discount_percent__gt=0).first()
    predefined = None
    if contact:
        amount = (Decimal(subtotal) * contact.discount_percent / 100).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
        predefined = {'name': contact.discount_remark or 'Predefined customer discount',
                      'percent': str(contact.discount_percent), 'threshold': '0', 'amount': str(amount),
                      'source': 'CUSTOMER', 'remark': contact.discount_remark or 'Predefined customer discount'}
    program = LoyaltyProgram.objects.filter(restaurant_id=branch.restaurant_id, enabled=True).first()
    if not program or not normalized_phone(phone):
        return predefined
    purchases = LoyaltyPurchase.objects.filter(customer__restaurant_id=branch.restaurant_id, customer__phone=phone)
    if exclude_order:
        purchases = purchases.exclude(order_id=exclude_order)
    spent = purchases.aggregate(total=Sum('amount'))['total'] or ZERO
    tier = eligible_tier(program.tiers, spent)
    if not tier:
        return predefined
    if predefined and Decimal(predefined['percent']) >= Decimal(tier['percent']):
        return predefined
    amount = (Decimal(subtotal) * Decimal(tier['percent']) / 100).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
    return {'name': tier['name'], 'percent': tier['percent'], 'threshold': tier['threshold'],
            'amount': str(amount)}


def discount(branch, phone, subtotal, manual=ZERO, exclude_order=None, saved=None):
    reward = saved if saved is not None else offer(branch, phone, subtotal, exclude_order)
    if reward:
        reward = {**reward, 'amount': str((Decimal(subtotal)*Decimal(reward['percent'])/100).quantize(Decimal('.01'), rounding=ROUND_HALF_UP))}
    # Offers do not stack: customers receive the larger authorized discount.
    applied = reward if reward and Decimal(reward['amount']) >= Decimal(manual) else None
    return max(Decimal(manual), Decimal(reward['amount']) if reward else ZERO), applied


@transaction.atomic
def record_purchase(order):
    phone = normalized_phone(order.customer_phone)
    if not phone:
        LoyaltyPurchase.objects.filter(order=order).delete()
        return
    customer, _ = LoyaltyCustomer.objects.get_or_create(restaurant_id=order.branch.restaurant_id, phone=phone)
    customer = LoyaltyCustomer.objects.select_for_update().get(pk=customer.pk)
    if order.customer_name and order.customer_name not in ('Guest', 'Walk-in Guest'):
        customer.name = order.customer_name[:150]
    customer.save()
    settled = order.paid_amount >= order.total_payable if order.is_pos_managed else order.payment_status == 'PAID'
    amount = ZERO
    if settled and order.status != 'CANCELLED':
        tip = Decimal((order.pricing_policy or {}).get('customer_tip', '0'))
        amount = max(ZERO, order.total_payable - order.refunded_amount - tip)
    LoyaltyPurchase.objects.update_or_create(order=order, defaults={'customer': customer, 'amount': amount})


@transaction.atomic
def save_program(branch, values):
    from apps.restaurants.models import Restaurant
    from apps.orders.pos_services import Conflict
    Restaurant.objects.select_for_update().get(pk=branch.restaurant_id)
    program, _ = LoyaltyProgram.objects.get_or_create(restaurant_id=branch.restaurant_id)
    if values['version'] != program.version:
        raise Conflict('Loyalty rules changed. Reload them before saving.')
    program.enabled, program.tiers = values['enabled'], values['tiers']
    program.version += 1
    program.save()
    return program


def discount_reason(reward):
    if reward.get("source") == "CUSTOMER":
        return f"Customer discount: {reward['percent']}% - {reward['remark']}"
    return f"Loyalty: {reward['name']} ({reward['percent']}%)"
