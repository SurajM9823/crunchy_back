"""Transactional catalog writes with durable revisions and live-update events."""
from decimal import Decimal
from django.db import transaction
from django.db.models import F
from rest_framework.exceptions import ValidationError
from apps.restaurants.models import Branch, Restaurant
from apps.inventory.models import RecipeItem, InventoryItem
from .models import (Category, Product, ProductVariant, ModifierGroup, ModifierOption,
    OutletProductOverride, OutletTimePricingSchedule, MenuRevision, MenuOutboxEvent)
from .pricing_engine import round_currency


@transaction.atomic
def menu_changed(*, restaurant_id=None, branch_id=None):
    branches = Branch.objects.filter(pk=branch_id) if branch_id else Branch.objects.filter(restaurant_id=restaurant_id)
    for bid in branches.order_by('pk').values_list('pk', flat=True):
        state, _ = MenuRevision.objects.get_or_create(branch_id=bid)
        MenuRevision.objects.filter(pk=bid).update(revision=F('revision') + 1)
        state.refresh_from_db()
        MenuOutboxEvent.objects.create(branch_id=bid, revision=state.revision)


def invalidate_outlet_menu_cache(branch_id):
    menu_changed(branch_id=branch_id)


def broadcast_product_availability_change(branch_id, product_id, is_available, product_name=''):
    # Compatibility for inventory mutations: durable publication after commit.
    menu_changed(branch_id=branch_id)


@transaction.atomic
def category_create(name, restaurant=None, **data):
    if restaurant is None:
        candidates = list(Restaurant.objects.all()[:2])
        if len(candidates) != 1:
            raise ValidationError('Specify the restaurant for this category.')
        restaurant = candidates[0]
    category = Category(name=name.strip(), restaurant=restaurant, **data)
    category.save(force_insert=True)
    menu_changed(restaurant_id=category.restaurant_id)
    return category


@transaction.atomic
def category_update(category, **data):
    category = Category.objects.select_for_update().get(pk=category.pk)
    data.pop('id', None)
    for key, value in data.items():
        setattr(category, key, value)
    category.save()
    menu_changed(restaurant_id=category.restaurant_id)
    return category


def _combo(product):
    if not product.is_combo_package:
        product.combo_items = []
        product.combo_original_price = None
        return
    if not product.combo_items:
        raise ValidationError({'combo_items': 'A combo needs at least one item.'})
    ids = [row['product_id'] for row in product.combo_items]
    items = {p.id: p for p in Product.objects.filter(id__in=ids,
        category__restaurant_id=product.category.restaurant_id, is_archived=False,
        category__is_archived=False, is_combo_package=False)}
    if len(set(ids)) != len(ids) or set(ids) != set(items) or product.pk in ids:
        raise ValidationError({'combo_items': 'Use unique, active, non-combo products from this restaurant.'})
    total = sum((items[row['product_id']].base_price * row['quantity'] for row in product.combo_items), Decimal('0'))
    product.combo_original_price = total
    if total > Decimal('99999999.99'):
        raise ValidationError({'combo_items': 'The package value exceeds the supported monetary range.'})
    value = product.combo_discount_value
    if value is None or value < 0 or product.combo_discount_type not in dict(Product.COMBO_DISCOUNT_TYPES):
        raise ValidationError({'combo_discount_value': 'A valid discount type and nonnegative value are required.'})
    if product.combo_discount_type == 'percentage':
        if value > 100:
            raise ValidationError({'combo_discount_value': 'Percentage cannot exceed 100.'})
        price = total * (1 - value / 100)
    elif product.combo_discount_type == 'fixed_price':
        price = value
    else:
        price = max(Decimal('0'), total - value)
    product.base_price = round_currency(price)
    product.combo_items = [{**row, 'product_name': items[row['product_id']].name,
        'unit_price': str(items[row['product_id']].base_price)} for row in product.combo_items]


def _upsert(model, scope, row):
    row = dict(row)
    pk = row.pop('id', None)
    obj = model.objects.filter(pk=pk, **scope).first() if pk else None
    if not obj:
        if pk and model.objects.filter(pk=pk).exists():
            raise ValidationError('Nested ID belongs to another product.')
        obj = model(id=pk or '', **scope)
    for key, value in row.items():
        setattr(obj, key, value)
    obj.save()
    return obj


def _sync_rows(model, scope, rows):
    ids = [row.get('id') for row in rows if row.get('id')]
    if len(ids) != len(set(ids)):
        raise ValidationError('Duplicate nested IDs.')
    keep = [_upsert(model, scope, row).pk for row in rows]
    model.objects.filter(**scope).exclude(pk__in=keep).delete()


def _sync_children(product, data, branch):
    if 'variants' in data:
        _sync_rows(ProductVariant, {'product': product}, data['variants'])
    if 'modifier_groups' in data:
        keep = []
        for row in data['modifier_groups']:
            row = dict(row)
            options = row.pop('options', [])
            group = _upsert(ModifierGroup, {'product': product}, row)
            if group.pk in keep:
                raise ValidationError('Duplicate modifier groups.')
            keep.append(group.pk)
            _sync_rows(ModifierOption, {'group': group}, options)
        product.modifier_groups.exclude(pk__in=keep).delete()
    if 'recipe_ingredients' in data:
        if not branch:
            raise ValidationError('An outlet is required for inventory recipes.')
        rows = data['recipe_ingredients']
        ids = [r['inventory_item_id'] for r in rows]
        if len(ids) != len(set(ids)) or InventoryItem.objects.filter(pk__in=ids, branch=branch, is_active=True).count() != len(ids):
            raise ValidationError({'recipe_ingredients': 'Ingredients must be unique and belong to this outlet.'})
        product.recipe_items.filter(inventory_item__branch=branch, variant__isnull=True).delete()
        RecipeItem.objects.bulk_create([RecipeItem(product=product, **row) for row in rows])


def _save_product(product, data, branch=None, creating=False):
    linked_id = data.pop('linked_inventory_item', None)
    nested = {key: data.pop(key) for key in ('variants', 'modifier_groups', 'recipe_ingredients') if key in data}
    if not creating:
        data.pop('id', None)
    data.pop('combo_original_price', None)
    for key, value in data.items():
        setattr(product, key, value)
    if product.category.is_archived:
        raise ValidationError({'category': 'Restore this category before editing products.'})
    if branch and product.category.restaurant_id != branch.restaurant_id:
        raise ValidationError({'category': 'Category belongs to a different restaurant.'})
    if linked_id:
        if not branch or not InventoryItem.objects.filter(pk=linked_id, branch=branch, is_active=True).exists():
            raise ValidationError({'linked_inventory_item': 'Inventory item must belong to this outlet.'})
        product.is_direct_inventory_item = True
        product.requires_kitchen = False
        nested['recipe_ingredients'] = [{'inventory_item_id': linked_id, 'quantity_required': Decimal('1')}]
    _combo(product)
    product.save(force_insert=creating)
    if product.is_combo_package and 'variants' in nested:
        for row in nested['variants']:
            row['price'] = product.base_price
    _sync_children(product, nested, branch)
    for combo in Product.objects.filter(category__restaurant_id=product.category.restaurant_id, is_combo_package=True, is_archived=False).exclude(pk=product.pk):
        if any(row['product_id'] == product.pk for row in combo.combo_items):
            _combo(combo)
            combo.save()
    menu_changed(restaurant_id=product.category.restaurant_id)
    product._prefetched_objects_cache = {}
    return product


@transaction.atomic
def product_create(category, name, base_price, branch=None, **data):
    Restaurant.objects.select_for_update().get(pk=category.restaurant_id)
    return _save_product(Product(category=category, name=name.strip(), base_price=Decimal(str(base_price))), data, branch, True)


@transaction.atomic
def product_update(product, branch=None, **data):
    Restaurant.objects.select_for_update().get(pk=product.category.restaurant_id)
    product = Product.objects.select_for_update().select_related('category').get(pk=product.pk)
    return _save_product(product, data, branch)


@transaction.atomic
def product_archive(product):
    Restaurant.objects.select_for_update().get(pk=product.category.restaurant_id)
    for combo in Product.objects.filter(category__restaurant_id=product.category.restaurant_id, is_combo_package=True, is_archived=False):
        if any(row['product_id'] == product.pk for row in combo.combo_items):
            raise ValidationError('Remove this product from active combos before archiving it.')
    product.is_archived = True
    product.save(update_fields=['is_archived', 'updated_at'])
    menu_changed(restaurant_id=product.category.restaurant_id)


@transaction.atomic
def variant_create(product, **data):
    obj = _upsert(ProductVariant, {'product': product}, data)
    menu_changed(restaurant_id=product.category.restaurant_id)
    return obj


@transaction.atomic
def modifier_group_create(product, **data):
    obj = _upsert(ModifierGroup, {'product': product}, data)
    menu_changed(restaurant_id=product.category.restaurant_id)
    return obj


@transaction.atomic
def modifier_option_create(group, **data):
    obj = _upsert(ModifierOption, {'group': group}, data)
    menu_changed(restaurant_id=group.product.category.restaurant_id)
    return obj


@transaction.atomic
def outlet_update_product(branch, product, **data):
    if product.category.restaurant_id != branch.restaurant_id:
        raise ValidationError('Product belongs to another restaurant.')
    obj, _ = OutletProductOverride.objects.update_or_create(branch=branch, product=product, defaults=data)
    menu_changed(branch_id=branch.pk)
    return obj


def outlet_toggle_product_availability(branch, product, is_available):
    return outlet_update_product(branch, product, is_available=is_available)


def outlet_override_product_price(branch, product, price_override=None):
    return outlet_update_product(branch, product, price_override=price_override)


@transaction.atomic
def schedule_save(branch, data, instance=None):
    obj = instance or OutletTimePricingSchedule(branch=branch)
    for key, value in data.items():
        setattr(obj, key, value)
    if Product.objects.filter(pk__in=obj.product_ids, category__restaurant_id=branch.restaurant_id, is_archived=False).count() != len(set(obj.product_ids)):
        raise ValidationError({'product_ids': 'Unknown product or product belongs to another restaurant.'})
    obj.save()
    menu_changed(branch_id=branch.pk)
    return obj


@transaction.atomic
def schedule_delete(instance):
    bid = instance.branch_id
    instance.delete()
    menu_changed(branch_id=bid)
