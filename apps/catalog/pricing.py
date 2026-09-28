"""Pure price resolution shared by menu snapshots, quotes and checkout."""
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from .pricing_engine import round_currency, calculate_dynamic_combo_price

NPT = ZoneInfo('Asia/Kathmandu')
DAYS = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')


def next_price_change(products, schedules, channel, now):
    """Next actual pricing boundary; static menus require no timed REST refresh."""
    windows = [(s.start_time, s.end_time, s.days) for s in schedules
               if s.is_active and (not s.channels or channel in s.channels)]
    for product in products:
        for slot in product.time_pricings.all():
            if slot.is_active:
                days = DAYS if slot.days == 'All Days' else DAYS[:5] if slot.days == 'Mon-Fri' else [d.strip() for d in slot.days.split(',')]
                windows.append((slot.start_time, slot.end_time, days))
    local = now.astimezone(NPT)
    boundaries = []
    for offset in range(-1, 8):
        day = local.date() + timedelta(days=offset)
        for start, end, days in windows:
            if DAYS[day.weekday()] not in days:
                continue
            for boundary in (datetime.combine(day, start, NPT),
                             datetime.combine(day + timedelta(days=int(end <= start)), end, NPT)):
                if boundary > local:
                    boundaries.append(boundary.timestamp())
    return min(boundaries) if boundaries else None


def active_window(start, end, days, now):
    local = now.astimezone(NPT)
    clock = local.time().replace(tzinfo=None)
    if start < end:
        return DAYS[local.weekday()] in days and start <= clock < end
    # An overnight window belongs to the day on which it starts.
    anchor = local if clock >= start else local - timedelta(days=1)
    return DAYS[anchor.weekday()] in days and (clock >= start or clock < end)


def applicable_schedule(product, schedules, channel, now):
    eligible = [s for s in schedules if s.is_active and (not s.channels or channel in s.channels)
                and (not s.product_ids or product.id in s.product_ids)
                and active_window(s.start_time, s.end_time, s.days, now)]
    return max(eligible, key=lambda s: (s.priority, s.pk), default=None)


def visible(product, override, channel):
    attr = {'pos': 'show_on_pos', 'qr': 'show_on_qr', 'web': 'is_web_visible',
            'kiosk': 'show_on_pos', 'delivery': 'is_web_visible'}.get(channel)
    value = getattr(override, attr, None) if override and attr else None
    allowed = value if value is not None else getattr(product, attr, True) if attr else True
    return bool(allowed and (channel != 'delivery' or product.is_delivery_eligible))


def available(product, override):
    return product.is_available and not product.is_archived and not product.category.is_archived and (not override or override.is_available)


def base_price(product, override):
    return override.price_override if override and override.price_override is not None else product.base_price


def combo_baseline(product, products, overrides):
    total = Decimal('0')
    for row in product.combo_items:
        component = products.get(row['product_id'])
        if not component or component.is_combo_package or component.is_archived or component.category.is_archived:
            raise ValidationError('Combo contains a retired component.')
        total += base_price(component, overrides.get(component.pk)) * row['quantity']
    value = product.combo_discount_value or Decimal('0')
    kind = product.combo_discount_type
    price = total * (1 - value / 100) if kind == 'percentage' else value if kind == 'fixed_price' else max(Decimal('0'), total - value)
    return round_currency(price), round_currency(total)


def item_price(product, override, schedules, channel, now=None, variant=None, modifiers=(), combo_price=None):
    now = now or timezone.now()
    amount = combo_price if combo_price is not None else base_price(product, override)
    # Outlet override adjusts every variant by the same difference; default stays aligned.
    if variant and not product.is_combo_package:
        amount = max(Decimal('0'), variant.price + amount - product.base_price)
    slots = sorted(product.time_pricings.all(), key=lambda s: s.pk, reverse=True)
    for slot in slots:
        if slot.days == 'All Days':
            days = DAYS
        elif slot.days == 'Mon-Fri':
            days = DAYS[:5]
        else:
            days = [d.strip() for d in slot.days.split(',')]
        if slot.is_active and active_window(slot.start_time, slot.end_time, days, now):
            amount = slot.price
            break
    schedule = applicable_schedule(product, schedules, channel, now)
    if schedule:
        adjustment = schedule.adjustment_percentage
        if adjustment is None:
            adjustment = -schedule.discount_percentage
        amount *= 1 + adjustment / 100
    amount *= 1 - product.discount_percent / 100
    return max(Decimal('0'), round_currency(amount + sum((m.price_delta for m in modifiers), Decimal('0'))))


def resolve_choices(product, raw):
    variants = list(product.variants.all())
    variant_id = raw.get('variant_id')
    variant = next((v for v in variants if v.pk == variant_id), None) if variant_id else next((v for v in variants if v.is_default), None)
    if variant_id and not variant:
        raise ValidationError('Variant does not belong to this product.')
    ids = raw.get('modifier_option_ids', [])
    if len(ids) != len(set(ids)):
        raise ValidationError('Duplicate modifier choices.')
    options = {o.pk: o for g in product.modifier_groups.all() for o in g.options.all()}
    if any(pk not in options for pk in ids):
        raise ValidationError('Modifier does not belong to this product.')
    for group in product.modifier_groups.all():
        count = sum(pk in ids for pk in (o.pk for o in group.options.all()))
        if not max(group.min_selections, int(group.required)) <= count <= group.max_selections:
            raise ValidationError(f'Choose between {max(group.min_selections, int(group.required))} and {group.max_selections} options for {group.name}.')
    return variant, [options[pk] for pk in ids]
