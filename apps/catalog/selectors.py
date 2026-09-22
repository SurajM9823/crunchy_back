from decimal import Decimal
from django.core.cache import cache
from django.db.models import Prefetch
from .models import (
    Category,
    Product,
    ProductVariant,
    ModifierGroup,
    ModifierOption,
    OutletProductOverride,
    OutletTimePricingSchedule,
)


MENU_CACHE_TTL = 300  # 5 minutes TTL for 10k concurrent request protection


def list_categories(include_archived: bool = False):
    """
    Returns categories ordered by display_order.
    """
    qs = Category.objects.all()
    if not include_archived:
        qs = qs.filter(is_archived=False)
    return qs.order_by('display_order', 'name')


def get_category_by_id(category_id: str) -> Category:
    return Category.objects.filter(id=category_id).first()


def get_product_by_id(product_id: str) -> Product:
    return (
        Product.objects
        .prefetch_related(
            'variants',
            Prefetch('modifier_groups', queryset=ModifierGroup.objects.prefetch_related('options')),
            'time_pricings',
        )
        .filter(id=product_id)
        .first()
    )


def list_products(
    category_id: str = None,
    active_only: bool = True,
    channel: str = None,
):
    """
    Lists global products with pre-fetched variants and modifier groups.
    """
    qs = Product.objects.select_related('category').prefetch_related(
        'variants',
        Prefetch('modifier_groups', queryset=ModifierGroup.objects.prefetch_related('options')),
    )

    if category_id:
        qs = qs.filter(category_id=category_id)
    if active_only:
        qs = qs.filter(is_available=True)

    if channel == 'pos':
        qs = qs.filter(show_on_pos=True)
    elif channel == 'qr':
        qs = qs.filter(show_on_qr=True)
    elif channel == 'web':
        qs = qs.filter(is_web_visible=True)
    elif channel == 'delivery':
        qs = qs.filter(is_delivery_eligible=True)

    return qs.order_by('category__display_order', 'name')


def get_outlet_product_override(branch_id: int, product_id: str) -> OutletProductOverride:
    return OutletProductOverride.objects.filter(branch_id=branch_id, product_id=product_id).first()


def get_outlet_menu(branch_id: int, channel: str = 'all', force_refresh: bool = False) -> dict:
    """
    High-Scale Cache-Aside Menu Selector (Rule 13 - 10k Concurrency Mindset):
    Serves instantaneous, pre-compiled JSON menus from Redis.
    Applies outlet-specific stock status (is_available) and price overrides.
    Target latency: < 50ms.
    """
    cache_key = f"outlet:{branch_id}:menu:{channel}"
    if not force_refresh:
        cached_menu = cache.get(cache_key)
        if cached_menu:
            return cached_menu

    # 1. Fetch all active categories
    categories = list(Category.objects.filter(is_archived=False).order_by('display_order', 'name'))
    cat_map = {cat.id: {
        'id': cat.id,
        'name': cat.name,
        'icon_name': cat.icon_name,
        'display_order': cat.display_order,
        'hsn_code': cat.hsn_code,
        'products': [],
    } for cat in categories}

    # 2. Fetch outlet overrides for this branch
    overrides = {
        ov.product_id: ov
        for ov in OutletProductOverride.objects.filter(branch_id=branch_id)
    }

    # 3. Fetch products with variants & modifier groups
    products = (
        Product.objects
        .select_related('category')
        .prefetch_related(
            'variants',
            Prefetch('modifier_groups', queryset=ModifierGroup.objects.prefetch_related('options')),
        )
        .filter(category__is_archived=False)
        .order_by('name')
    )

    for prod in products:
        # Check channel visibility
        if channel == 'pos' and not prod.show_on_pos:
            continue
        elif channel == 'qr' and not prod.show_on_qr:
            continue
        elif channel == 'web' and not prod.is_web_visible:
            continue
        elif channel == 'delivery' and not prod.is_delivery_eligible:
            continue

        # Apply outlet override if exists
        override = overrides.get(prod.id)
        is_available = override.is_available if override else prod.is_available
        effective_price = str(override.price_override if (override and override.price_override is not None) else prod.base_price)

        variants_data = [
            {
                'id': var.id,
                'name': var.name,
                'price': str(var.price),
                'is_default': var.is_default,
            }
            for var in prod.variants.all()
        ]

        modifier_groups_data = [
            {
                'id': group.id,
                'name': group.name,
                'min_selections': group.min_selections,
                'max_selections': group.max_selections,
                'required': group.required,
                'options': [
                    {
                        'id': opt.id,
                        'name': opt.name,
                        'price_delta': str(opt.price_delta),
                        'is_default': opt.is_default,
                    }
                    for opt in group.options.all()
                ]
            }
            for group in prod.modifier_groups.all()
        ]

        product_payload = {
            'id': prod.id,
            'category_id': prod.category_id,
            'name': prod.name,
            'description': prod.description,
            'base_price': effective_price,
            'original_catalog_price': str(prod.base_price),
            'prep_time_minutes': prod.prep_time_minutes,
            'calories': prod.calories,
            'dietary_tags': prod.dietary_tags,
            'images': prod.images,
            'main_image_index': prod.main_image_index,
            'is_delivery_eligible': prod.is_delivery_eligible,
            'is_available': is_available,
            'is_web_visible': prod.is_web_visible,
            'show_on_pos': prod.show_on_pos,
            'show_on_qr': prod.show_on_qr,
            'discount_percent': str(prod.discount_percent),
            'requires_kitchen': prod.requires_kitchen,
            'is_counter_direct': prod.is_counter_direct,
            'is_combo_package': prod.is_combo_package,
            'combo_discount_type': prod.combo_discount_type,
            'combo_discount_value': str(prod.combo_discount_value) if prod.combo_discount_value else None,
            'combo_original_price': str(prod.combo_original_price) if prod.combo_original_price else None,
            'combo_items': prod.combo_items,
            'variants': variants_data,
            'modifier_groups': modifier_groups_data,
        }

        if prod.category_id in cat_map:
            cat_map[prod.category_id]['products'].append(product_payload)

    compiled_menu = {
        'branch_id': branch_id,
        'channel': channel,
        'categories': [cat for cat in cat_map.values() if cat['products'] or not channel],
    }

    # Cache pre-compiled JSON payload for 5 minutes
    cache.set(cache_key, compiled_menu, timeout=MENU_CACHE_TTL)
    return compiled_menu

