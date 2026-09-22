import math
from decimal import Decimal, ROUND_HALF_UP


VAT_RATE_DEFAULT = Decimal('13.00')  # Nepal statutory 13% VAT
COMBO_MINIMUM_FLOOR = Decimal('150.00')  # Clamped minimum combo price in NPR
COMBO_REMOVAL_CREDIT_RATE = Decimal('0.75')  # 75% credit when removing default item
COMBO_EXTRA_ITEM_DISCOUNT_RATE = Decimal('0.10')  # 10% discount on add-ons inside combo


def round_currency(value: Decimal) -> Decimal:
    """
    Rounds standard financial currency amounts to 2 decimal places using ROUND_HALF_UP.
    """
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def calculate_vat_breakdown(subtotal: Decimal, vat_rate: Decimal = VAT_RATE_DEFAULT) -> dict:
    """
    Computes tax-inclusive statutory VAT breakdown (Nepal Statutory 13% VAT Rule).
    Menu prices are tax-inclusive:
      vat_amount = round(subtotal * (vat_rate / (100 + vat_rate)), 2)
      net_subtotal = subtotal - vat_amount
    """
    subtotal = round_currency(subtotal)
    rate = Decimal(str(vat_rate))

    # Formula: Subtotal * (13 / 113)
    vat_factor = rate / (Decimal('100.00') + rate)
    vat_amount = round_currency(subtotal * vat_factor)
    net_amount = subtotal - vat_amount

    return {
        'gross_subtotal': subtotal,
        'vat_rate_percent': rate,
        'vat_amount': vat_amount,
        'net_amount': net_amount,
    }


def calculate_cash_round_down(total_amount: Decimal) -> dict:
    """
    Statutory Cash Currency Rule (Front-End Specification 2.3):
    In cash payment modes, subtotal fractions are rounded down to the nearest whole integer:
      cash_round_down_savings = total - floor(total)
      final_cash_total = total - cash_round_down_savings
    """
    total_amount = round_currency(total_amount)
    floor_integer = Decimal(math.floor(total_amount))
    savings = round_currency(total_amount - floor_integer)

    return {
        'original_total': total_amount,
        'cash_round_down_savings': savings,
        'final_cash_total': floor_integer,
    }


def calculate_line_item_unit_price(
    base_price: Decimal,
    variant_price: Decimal = None,
    modifier_price_deltas: list = None,
    time_slot_price: Decimal = None,
    discount_percent: Decimal = Decimal('0.00'),
) -> Decimal:
    """
    Pricing Hierarchy Formula (Front-End Specification 2.1):
      Unit Price = (Time Slot Price or Variant Price or Base Price) + Sum(Modifier Deltas)
      Discount is applied on the calculated item portion.
    """
    # 1. Determine base item portion
    if time_slot_price is not None:
        item_price = Decimal(str(time_slot_price))
    elif variant_price is not None:
        item_price = Decimal(str(variant_price))
    else:
        item_price = Decimal(str(base_price))

    # 2. Add modifier price deltas
    total_modifiers = Decimal('0.00')
    if modifier_price_deltas:
        for delta in modifier_price_deltas:
            total_modifiers += Decimal(str(delta))

    # 3. Apply promotional discount if any
    if discount_percent and Decimal(str(discount_percent)) > Decimal('0.00'):
        pct = Decimal(str(discount_percent)) / Decimal('100.00')
        discount_amount = round_currency(item_price * pct)
        item_price = max(Decimal('0.00'), item_price - discount_amount)

    unit_price = round_currency(item_price + total_modifiers)
    return max(Decimal('0.00'), unit_price)


def calculate_dynamic_combo_price(
    base_combo_price: Decimal,
    removed_items_base_prices: list = None,
    extra_items_prices: list = None,
    variant_upgrades_deltas: list = None,
) -> Decimal:
    """
    Dynamic Meal Kit / Combo Configurator Pricing Rules (Front-End Specification 4.2):
    - Removing default item: gives customer 75% credit of that item's base price.
        deduction = round(item.base_price * 0.75)
    - Variant upgrades: customer pays exact difference.
    - Adding extra items/units: automatically receives 10% combo discount:
        extra_cost = round((item_price) * 0.90)
    - Clamped Floor: Final combo price is never less than NPR 150.
        final_price = max(150, calculated_price)
    """
    current_price = Decimal(str(base_combo_price))

    # 1. Credit for removed default items (75% credit)
    if removed_items_base_prices:
        for item_base in removed_items_base_prices:
            credit = round_currency(Decimal(str(item_base)) * COMBO_REMOVAL_CREDIT_RATE)
            current_price -= credit

    # 2. Add variant/sauce upgrade deltas
    if variant_upgrades_deltas:
        for delta in variant_upgrades_deltas:
            current_price += Decimal(str(delta))

    # 3. Add extra items with 10% combo bundle discount
    if extra_items_prices:
        multiplier = Decimal('1.00') - COMBO_EXTRA_ITEM_DISCOUNT_RATE
        for extra_price in extra_items_prices:
            discounted = round_currency(Decimal(str(extra_price)) * multiplier)
            current_price += discounted

    # 4. Enforce NPR 150 Price Floor Clamp
    final_price = max(COMBO_MINIMUM_FLOOR, round_currency(current_price))
    return final_price

