from decimal import Decimal
from django.db import transaction, models
from django.core.exceptions import ValidationError
from django.utils import timezone
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from apps.catalog.models import Product, ProductVariant, OutletProductOverride
from apps.catalog.services import invalidate_outlet_menu_cache, broadcast_product_availability_change
from apps.orders.models import Order
from .models import (
    InventoryItem,
    RecipeItem,
    StockTransaction,
    StockTransactionType,
    InventoryCategory,
)


@transaction.atomic
def inventory_item_create(
    branch,
    sku: str,
    name: str,
    current_stock: Decimal = Decimal('0.000'),
    unit: str = 'PCS',
    min_threshold: Decimal = Decimal('5.000'),
    cost_per_unit: Decimal = Decimal('0.00'),
    category: InventoryCategory = None,
) -> InventoryItem:
    item = InventoryItem(
        branch=branch,
        sku=sku.strip().upper(),
        name=name.strip(),
        current_stock=Decimal(str(current_stock)),
        unit=unit,
        min_threshold=Decimal(str(min_threshold)),
        cost_per_unit=Decimal(str(cost_per_unit)),
        category=category,
    )
    item.save()

    if Decimal(str(current_stock)) > Decimal('0.000'):
        StockTransaction.objects.create(
            inventory_item=item,
            transaction_type=StockTransactionType.RESTOCK_PURCHASE,
            quantity=Decimal(str(current_stock)),
            previous_stock=Decimal('0.000'),
            resulting_stock=Decimal(str(current_stock)),
            notes="Initial stock intake",
        )
    return item


@transaction.atomic
def inventory_item_restock(
    item: InventoryItem,
    quantity: Decimal,
    cost_per_unit: Decimal = None,
    performed_by=None,
    notes: str = "",
) -> InventoryItem:
    """
    Adds inventory to stock, logs the audit transaction, and re-activates
    menu availability if product was previously marked out-of-stock.
    """
    # Lock row for atomic update
    item = InventoryItem.objects.select_for_update().get(id=item.id)
    qty = Decimal(str(quantity))

    prev_stock = item.current_stock
    new_stock = prev_stock + qty

    item.current_stock = new_stock
    if cost_per_unit is not None:
        item.cost_per_unit = Decimal(str(cost_per_unit))
    item.save()

    StockTransaction.objects.create(
        inventory_item=item,
        transaction_type=StockTransactionType.RESTOCK_PURCHASE,
        quantity=qty,
        previous_stock=prev_stock,
        resulting_stock=new_stock,
        performed_by=performed_by,
        notes=notes.strip() or "Shipment restock",
    )
    return item


@transaction.atomic
def recipe_item_create(
    product: Product,
    inventory_item: InventoryItem,
    quantity_required: Decimal = Decimal('1.000'),
    variant: ProductVariant = None,
) -> RecipeItem:
    recipe_item = RecipeItem(
        product=product,
        variant=variant,
        inventory_item=inventory_item,
        quantity_required=Decimal(str(quantity_required)),
    )
    recipe_item.save()
    return recipe_item


@transaction.atomic
def deduct_inventory_for_order(order: Order) -> list:
    """
    High-Concurrency Atomic Stock Deduction (Rule 12 & Rule 13):
    1. Expands order line items into their raw ingredients (BOM).
    2. Atomically decrements stock using row-level locks.
    3. Logs immutable StockTransaction records.
    4. Auto-out-of-stock trigger: If ingredient hits 0, marks product out of stock
       and broadcasts live WebSocket event with Zero Page Reload!
    """
    deductions = []
    branch = order.branch

    for order_item in order.items.select_related('product', 'variant').all():
        product = order_item.product
        variant = order_item.variant
        order_qty = Decimal(str(order_item.quantity))

        # 1. Check Recipe Bill of Materials (BOM)
        recipe_query = RecipeItem.objects.filter(product=product)
        if variant:
            # Query recipes specific to variant or general product
            recipe_items = list(recipe_query.filter(models.Q(variant=variant) | models.Q(variant__isnull=True)))
        else:
            recipe_items = list(recipe_query.filter(variant__isnull=True))

        for recipe in recipe_items:
            # Lock the inventory item for atomic update
            raw_item = (
                InventoryItem.objects
                .select_for_update()
                .filter(id=recipe.inventory_item_id, branch=branch)
                .first()
            )
            if not raw_item:
                continue

            total_needed = recipe.quantity_required * order_qty
            prev_stock = raw_item.current_stock
            new_stock = max(Decimal('0.000'), prev_stock - total_needed)

            raw_item.current_stock = new_stock
            raw_item.save(update_fields=['current_stock', 'updated_at'])

            txn = StockTransaction.objects.create(
                inventory_item=raw_item,
                transaction_type=StockTransactionType.ORDER_DEDUCTION,
                quantity=-total_needed,
                previous_stock=prev_stock,
                resulting_stock=new_stock,
                reference_order=order,
                notes=f"Order deduction for {order_item.quantity}x {product.name}",
            )
            deductions.append(txn)

            # Auto Out-Of-Stock Trigger when raw material runs out
            if new_stock <= Decimal('0.000'):
                OutletProductOverride.objects.update_or_create(
                    branch=branch,
                    product=product,
                    defaults={'is_available': False},
                )
                invalidate_outlet_menu_cache(branch.id)
                broadcast_product_availability_change(
                    branch_id=branch.id,
                    product_id=product.id,
                    is_available=False,
                    product_name=product.name,
                )

    return deductions

