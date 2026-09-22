from decimal import Decimal
from django.db import transaction
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.utils import timezone
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from .models import (
    Category,
    Product,
    ProductVariant,
    ModifierGroup,
    ModifierOption,
    OutletProductOverride,
)


def invalidate_outlet_menu_cache(branch_id: int):
    """
    Purges cached menu representations for an outlet across all channels.
    """
    channels = ['all', 'pos', 'qr', 'web', 'delivery', 'kiosk']
    for ch in channels:
        cache.delete(f"outlet:{branch_id}:menu:{ch}")


def broadcast_product_availability_change(branch_id: int, product_id: str, is_available: bool, product_name: str = ""):
    """
    Broadcasts real-time WebSocket event to all connected POS, KDS, Kiosk, and QR clients.
    Zero Page Reload (Rule 3 & Principle 7):
    Clients reactively update item availability badge without browser refreshes.
    """
    channel_layer = get_channel_layer()
    if channel_layer:
        payload = {
            'type': 'product_availability_changed',
            'product_id': product_id,
            'product_name': product_name,
            'is_available': is_available,
            'branch_id': branch_id,
            'timestamp': timezone.now().isoformat(),
        }
        # Send to outlet menu group and operations group
        async_to_sync(channel_layer.group_send)(f"outlet_{branch_id}_menu", payload)
        async_to_sync(channel_layer.group_send)(f"outlet_{branch_id}_operations", payload)


@transaction.atomic
def category_create(
    name: str,
    id: str = None,
    icon_name: str = "Utensils",
    display_order: int = 0,
    hsn_code: str = "",
) -> Category:
    category = Category(
        name=name.strip(),
        icon_name=icon_name,
        display_order=display_order,
        hsn_code=hsn_code.strip(),
    )
    if id:
        category.id = id.strip()
    category.save()
    return category


@transaction.atomic
def category_update(category: Category, **kwargs) -> Category:
    for key, value in kwargs.items():
        if hasattr(category, key):
            setattr(category, key, value)
    category.save()
    return category


@transaction.atomic
def product_create(
    category: Category,
    name: str,
    base_price: Decimal,
    id: str = None,
    description: str = "",
    cost_price: Decimal = None,
    prep_time_minutes: int = 12,
    calories: int = None,
    dietary_tags: list = None,
    images: list = None,
    is_delivery_eligible: bool = True,
    is_available: bool = True,
    is_web_visible: bool = True,
    show_on_pos: bool = True,
    show_on_qr: bool = True,
    discount_percent: Decimal = Decimal('0.00'),
    requires_kitchen: bool = True,
    is_counter_direct: bool = False,
    is_direct_inventory_item: bool = False,
    is_combo_package: bool = False,
    combo_discount_type: str = None,
    combo_discount_value: Decimal = None,
    combo_original_price: Decimal = None,
    combo_items: list = None,
) -> Product:
    product = Product(
        category=category,
        name=name.strip(),
        base_price=Decimal(str(base_price)),
        description=description,
        cost_price=Decimal(str(cost_price)) if cost_price is not None else None,
        prep_time_minutes=prep_time_minutes,
        calories=calories,
        dietary_tags=dietary_tags or [],
        images=images or [],
        is_delivery_eligible=is_delivery_eligible,
        is_available=is_available,
        is_web_visible=is_web_visible,
        show_on_pos=show_on_pos,
        show_on_qr=show_on_qr,
        discount_percent=Decimal(str(discount_percent)),
        requires_kitchen=requires_kitchen,
        is_counter_direct=is_counter_direct,
        is_direct_inventory_item=is_direct_inventory_item,
        is_combo_package=is_combo_package,
        combo_discount_type=combo_discount_type,
        combo_discount_value=Decimal(str(combo_discount_value)) if combo_discount_value is not None else None,
        combo_original_price=Decimal(str(combo_original_price)) if combo_original_price is not None else None,
        combo_items=combo_items or [],
    )
    if id:
        product.id = id.strip()
    product.save()
    return product


@transaction.atomic
def product_update(product: Product, **kwargs) -> Product:
    for key, value in kwargs.items():
        if hasattr(product, key):
            setattr(product, key, value)
    product.save()
    return product


@transaction.atomic
def variant_create(
    product: Product,
    name: str,
    price: Decimal,
    id: str = None,
    is_default: bool = False,
) -> ProductVariant:
    variant = ProductVariant(
        product=product,
        name=name.strip(),
        price=Decimal(str(price)),
        is_default=is_default,
    )
    if id:
        variant.id = id.strip()
    variant.save()
    return variant


@transaction.atomic
def modifier_group_create(
    product: Product,
    name: str,
    id: str = None,
    min_selections: int = 0,
    max_selections: int = 1,
    required: bool = False,
) -> ModifierGroup:
    group = ModifierGroup(
        product=product,
        name=name.strip(),
        min_selections=min_selections,
        max_selections=max_selections,
        required=required,
    )
    if id:
        group.id = id.strip()
    group.save()
    return group


@transaction.atomic
def modifier_option_create(
    group: ModifierGroup,
    name: str,
    id: str = None,
    price_delta: Decimal = Decimal('0.00'),
    is_default: bool = False,
) -> ModifierOption:
    option = ModifierOption(
        group=group,
        name=name.strip(),
        price_delta=Decimal(str(price_delta)),
        is_default=is_default,
    )
    if id:
        option.id = id.strip()
    option.save()
    return option


@transaction.atomic
def outlet_toggle_product_availability(
    branch,
    product: Product,
    is_available: bool,
) -> OutletProductOverride:
    """
    Outlet Admin Action:
    Marks a product available or sold out specifically for this branch outlet.
    Busts Redis cache and broadcasts real-time WebSocket update to all devices.
    """
    override, _ = OutletProductOverride.objects.get_or_create(
        branch=branch,
        product=product,
    )
    override.is_available = is_available
    override.save()

    # Invalidate cache for this branch
    invalidate_outlet_menu_cache(branch.id)

    # Dispatch live WebSocket notification for ZERO PAGE RELOAD
    broadcast_product_availability_change(
        branch_id=branch.id,
        product_id=product.id,
        is_available=is_available,
        product_name=product.name,
    )

    return override


@transaction.atomic
def outlet_override_product_price(
    branch,
    product: Product,
    price_override: Decimal = None,
) -> OutletProductOverride:
    """
    Outlet Admin Action:
    Sets a branch-specific price override (e.g. airport branch premium).
    If price_override is None, reverts to global catalog base price.
    """
    override, _ = OutletProductOverride.objects.get_or_create(
        branch=branch,
        product=product,
    )
    override.price_override = Decimal(str(price_override)) if price_override is not None else None
    override.save()

    invalidate_outlet_menu_cache(branch.id)
    return override

