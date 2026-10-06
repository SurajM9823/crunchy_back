"""Database reads and bounded, versioned menu materialization."""
import logging
import time
from django.core.cache import cache
from django.db.models import Prefetch
from django.utils import timezone
from rest_framework.exceptions import APIException, ValidationError
from apps.restaurants.models import Branch
from apps.inventory.models import RecipeItem
from .models import Category, Product, ModifierGroup, OutletProductOverride, OutletTimePricingSchedule, MenuRevision
from .serializers import ProductDetailSerializer, CategorySerializer, ScheduleSerializer
from .pricing import available, visible, item_price, combo_baseline, resolve_choices, base_price, next_price_change

logger = logging.getLogger(__name__)
MENU_CACHE_TTL = 120
CHANNELS = ('all', 'web', 'qr', 'pos', 'kiosk', 'delivery')


class MenuBuilding(APIException):
    status_code = 503
    default_detail = 'Menu is being prepared. Retry shortly.'
    wait = 1


def list_categories(include_archived=False, restaurant_id=None):
    qs = Category.objects.all()
    if restaurant_id is not None:
        qs = qs.filter(restaurant_id=restaurant_id)
    return qs if include_archived else qs.filter(is_archived=False)


def get_category_by_id(category_id, restaurant_id=None):
    return list_categories(True, restaurant_id).filter(pk=category_id).first()


def product_queryset():
    return Product.objects.select_related('category').prefetch_related('variants', 'time_pricings',
        Prefetch('modifier_groups', queryset=ModifierGroup.objects.prefetch_related('options')),
        Prefetch('recipe_items', queryset=RecipeItem.objects.select_related('inventory_item')))


def list_products(category_id=None, active_only=True, channel=None, restaurant_id=None):
    qs = product_queryset().filter(is_archived=False)
    if restaurant_id is not None:
        qs = qs.filter(category__restaurant_id=restaurant_id)
    if category_id:
        qs = qs.filter(category_id=category_id)
    if active_only:
        qs = qs.filter(is_available=True, category__is_archived=False)
    return qs.order_by('category__display_order', 'name', 'pk')


def get_product_by_id(product_id, restaurant_id=None):
    return list_products(active_only=False, restaurant_id=restaurant_id).filter(pk=product_id).first()


def get_outlet_product_override(branch_id, product_id):
    return OutletProductOverride.objects.filter(branch_id=branch_id, product_id=product_id).first()


def catalog_context(branch, product_ids=None):
    queryset = list_products(active_only=False, restaurant_id=branch.restaurant_id)
    if product_ids is not None:
        queryset = queryset.filter(pk__in=product_ids)
    products = {p.pk: p for p in queryset}
    if product_ids is not None:
        component_ids = {r['product_id'] for p in products.values() for r in p.combo_items} - products.keys()
        products.update({p.pk:p for p in list_products(active_only=False, restaurant_id=branch.restaurant_id).filter(pk__in=component_ids)})
    overrides_query = OutletProductOverride.objects.filter(branch=branch)
    if product_ids is not None:
        overrides_query = overrides_query.filter(product_id__in=products)
    overrides = {o.product_id: o for o in overrides_query}
    schedules = list(OutletTimePricingSchedule.objects.filter(branch=branch))
    return products, overrides, schedules


def stock_available(product, branch, quantity=1):
    # Recipe rows are prefetched. Only this outlet's base recipe constrains its menu.
    return all(r.inventory_item.current_stock >= r.quantity_required * quantity
               for r in product.recipe_items.all()
               if r.inventory_item.branch_id == branch.pk and r.variant_id is None)


def _compile_menu(branch, channel, revision, now):
    products, overrides, schedules = catalog_context(branch)
    cats = {c.pk: {**CategorySerializer(c).data, 'products': []}
            for c in list_categories(restaurant_id=branch.restaurant_id)}
    for product in products.values():
        override = overrides.get(product.pk)
        if product.category_id not in cats or not visible(product, override, channel):
            continue
        row = dict(ProductDetailSerializer(product).data)
        for key in ('cost_price', 'recipe_ingredients', 'linked_inventory_item'):
            row.pop(key, None)
        row['category_id'] = product.category_id
        row['is_available'] = available(product, override) and stock_available(product, branch)
        combo_price = None
        if product.is_combo_package:
            try:
                combo_price, original = combo_baseline(product, products, overrides)
                if override and override.price_override is not None:
                    combo_price = override.price_override
                row['combo_original_price'] = str(original)
                # Bundle membership is independent of standalone channel visibility.
                row['combo_products'] = []
                for part in product.combo_items:
                    component = products[part['product_id']]
                    detail = dict(ProductDetailSerializer(component).data)
                    for private in ('cost_price', 'recipe_ingredients', 'linked_inventory_item'):
                        detail.pop(private, None)
                    detail['base_price'] = str(base_price(component, overrides.get(component.pk)))
                    row['combo_products'].append(detail)
                row['is_available'] = row['is_available'] and all(
                    available(products[r['product_id']], overrides.get(r['product_id'])) and stock_available(products[r['product_id']], branch, r['quantity']) for r in product.combo_items)
            except ValidationError:
                row['is_available'] = False
                combo_price = product.base_price
        row['original_catalog_price'] = str(base_price(product, override))
        row['base_price'] = str(item_price(product, override, schedules, channel, now, combo_price=combo_price))
        # Prices in this public snapshot are final; clients must not apply discounts twice.
        row['discount_percent'] = '0.00'
        for v, serialized in zip(product.variants.all(), row['variants']):
            serialized['price'] = str(item_price(product, override, schedules, channel, now, variant=v, combo_price=combo_price))
        for attr in ('show_on_pos', 'show_on_qr', 'is_web_visible'):
            value = getattr(override, attr, None) if override else None
            if value is not None:
                row[attr] = value
        cats[product.category_id]['products'].append(row)
    return {'branch_id': branch.pk, 'channel': channel, 'revision': revision,
            'valid_until': next_price_change(products.values(), schedules, channel, now),
            'categories': [c for c in cats.values() if c['products']]}


def get_outlet_menu(branch_id, channel='all', force_refresh=False):
    if channel not in CHANNELS:
        raise ValidationError({'channel': 'Unknown menu channel.'})
    branch = Branch.objects.get(pk=branch_id, is_active=True)
    revision = MenuRevision.objects.filter(branch=branch).values_list('revision', flat=True).first() or 0
    now = timezone.now()
    key = f'menu:v3:{branch_id}:{revision}:{channel}:{int(now.timestamp()) // 60}'
    # force_refresh retained for internal compatibility; revision is the invalidation mechanism.
    try:
        cached = cache.get(key)
        if cached is not None:
            return cached
        acquired = cache.add(key + ':building', True, timeout=10)
    except Exception:
        logger.warning('Menu cache unavailable; reading authoritative database', exc_info=True)
        return _compile_menu(branch, channel, revision, now)
    if not acquired:
        for _ in range(5):
            time.sleep(0.01)
            cached = cache.get(key)
            if cached is not None:
                return cached
        raise MenuBuilding()
    # Lease expires naturally: never delete another worker's lock after a slow build.
    result = _compile_menu(branch, channel, revision, now)
    try:
        cache.set(key, result, timeout=MENU_CACHE_TTL)
    except Exception:
        logger.warning('Unable to cache compiled menu', exc_info=True)
    return result


def management_snapshot(branch):
    products = list_products(active_only=False, restaurant_id=branch.restaurant_id)
    return {'branch_id': branch.pk,
        'categories': CategorySerializer(list_categories(True, branch.restaurant_id), many=True).data,
        'products': ProductDetailSerializer(products, many=True, context={'branch': branch}).data,
        'schedules': ScheduleSerializer(OutletTimePricingSchedule.objects.filter(branch=branch).order_by('pk'), many=True).data,
        'overrides': {o.product_id: {'is_available': o.is_available, 'price_override': str(o.price_override) if o.price_override is not None else None}
                      for o in OutletProductOverride.objects.filter(branch=branch)}}


def quote_items(branch, items, channel, now=None):
    from decimal import Decimal
    now = now or timezone.now()
    selected_ids = {r['product_id'] for r in items}
    selected_ids.update(c['product_id'] for r in items for c in r.get('combo_selections', []))
    products, overrides, schedules = catalog_context(branch, selected_ids)
    result = []
    for raw in items:
        product = products.get(raw['product_id'])
        override = overrides.get(raw['product_id'])
        if product and not available(product, override):
            raise ValidationError(f"Item '{product.name}' is OUT OF STOCK at this outlet.")
        if not product or not visible(product, override, channel):
            raise ValidationError('Product is unavailable for this outlet and channel.')
        variant, modifiers = resolve_choices(product, raw)
        combo_price = None
        components = []
        if product.is_combo_package:
            combo_price, _ = combo_baseline(product, products, overrides)
            if override and override.price_override is not None:
                combo_price = override.price_override
        price = item_price(product, override, schedules, channel, now, variant, modifiers, combo_price)
        if product.is_combo_package:
            price, components = quote_combo_components(product, raw, price, products, overrides, schedules, channel, now)
        elif 'combo_selections' in raw:
            raise ValidationError('Combo selections require a combo product.')
        if price * raw['quantity'] > Decimal('99999999.99'):
            raise ValidationError('Line total exceeds the supported monetary range.')
        result.append({'product_id': product.pk, 'variant_id': variant.pk if variant else None,
                       'quantity': raw['quantity'], 'unit_price': str(price), 'line_total': str(price * raw['quantity']),
                       'combo_components': components})
    return {'branch_id': branch.pk, 'channel': channel, 'items': result,
            'subtotal': str(sum((Decimal(r['line_total']) for r in result), Decimal('0'))),
            'quoted_at': now.isoformat()}


def quote_combo_components(combo, raw, price, products, overrides, schedules, channel, now):
    from decimal import Decimal
    from .pricing_engine import calculate_dynamic_combo_price
    included = {r['product_id']: r['quantity'] for r in combo.combo_items}
    selections = raw.get('combo_selections')
    if selections is None:
        selections = [{'product_id': pid, 'quantity': qty, 'modifier_option_ids': [
            o.pk for g in products[pid].modifier_groups.all() for o in g.options.all() if o.is_default]}
            for pid, qty in included.items()]
    extras, upgrades, snapshot = [], [], []
    for selection in selections:
        product = products.get(selection['product_id'])
        override = overrides.get(selection['product_id'])
        if not product or product.is_combo_package or not available(product, override) or (product.pk not in included and not visible(product, override, channel)):
            raise ValidationError('A selected combo component is unavailable.')
        variant, modifiers = resolve_choices(product, selection)
        count = selection['quantity']
        covered = min(included.get(product.pk, 0), count)
        if count > covered and not visible(product, override, channel):
            raise ValidationError('An extra combo component is unavailable for this channel.')
        included[product.pk] = included.get(product.pk, 0) - covered
        default = next((v for v in product.variants.all() if v.is_default), None)
        delta = (variant.price if variant else product.base_price) - (default.price if default else product.base_price)
        # The advertised bundle includes its default recipe/choices. Only a
        # customization above those defaults changes the included-unit price.
        default_surcharge = sum((o.price_delta for g in product.modifier_groups.all()
                                 for o in g.options.all() if o.is_default), Decimal('0'))
        surcharge = max(Decimal('0'), sum((o.price_delta for o in modifiers), Decimal('0')) - default_surcharge)
        upgrades.extend([delta + surcharge] * covered)
        extra_price = item_price(product, override, schedules, channel, now, variant, modifiers)
        extras.extend([extra_price] * (count - covered))
        snapshot.append({'product_id': product.pk, 'product_name': product.name,
            'variant_id': variant.pk if variant else None, 'variant_name': variant.name if variant else '',
            'quantity': count, 'requires_kitchen': product.requires_kitchen,
            'modifiers': [{'id': o.pk, 'name': o.name, 'price_delta': str(o.price_delta)} for o in modifiers]})
    removed = [base_price(products[pid], overrides.get(pid)) for pid, count in included.items() for _ in range(count)]
    # The floor belongs to customization; an unmodified manager-defined cheap bundle retains its price.
    if 'combo_selections' in raw:
        price = calculate_dynamic_combo_price(price, removed, extras, upgrades)
    elif upgrades:
        price += sum(upgrades, Decimal('0'))
    return price, snapshot
