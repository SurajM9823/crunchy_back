from django.db.models import F
from .models import InventoryItem, RecipeItem, StockTransaction


def list_inventory_for_branch(
    branch_id: int,
    category_id: int = None,
    low_stock_only: bool = False,
):
    qs = (
        InventoryItem.objects
        .select_related('category', 'branch')
        .filter(branch_id=branch_id, is_active=True)
    )
    if category_id:
        qs = qs.filter(category_id=category_id)
    if low_stock_only:
        qs = qs.filter(current_stock__lte=F('min_threshold'))

    return qs.order_by('name')


def get_inventory_item_by_id(item_id: int) -> InventoryItem:
    return (
        InventoryItem.objects
        .select_related('category', 'branch')
        .filter(id=item_id)
        .first()
    )


def get_inventory_item_by_sku(branch_id: int, sku: str) -> InventoryItem:
    return (
        InventoryItem.objects
        .select_related('category', 'branch')
        .filter(branch_id=branch_id, sku=sku.strip().upper())
        .first()
    )


def list_low_stock_items(branch_id: int):
    return list_inventory_for_branch(branch_id=branch_id, low_stock_only=True)


def list_recipes_for_product(product_id: str):
    return (
        RecipeItem.objects
        .select_related('product', 'variant', 'inventory_item')
        .filter(product_id=product_id)
    )


def list_stock_transactions(branch_id: int, item_id: int = None, limit: int = 50):
    qs = (
        StockTransaction.objects
        .select_related('inventory_item', 'reference_order', 'performed_by')
        .filter(inventory_item__branch_id=branch_id)
    )
    if item_id:
        qs = qs.filter(inventory_item_id=item_id)
    return qs.order_by('-created_at')[:limit]

