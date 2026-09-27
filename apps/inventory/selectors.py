from decimal import Decimal
from django.db.models import F, Q, Sum, DecimalField
from .models import (
    InventoryCategory,
    InventoryItem,
    RecipeItem,
    StockTransaction,
    Supplier,
    PurchaseInvoice,
    StockMovementLedger,
)


def list_suppliers_for_branch(branch_id: int, search: str = None):
    qs = Supplier.objects.filter(branch_id=branch_id, is_active=True)
    if search:
        search = search.strip()
        qs = qs.filter(
            Q(name__icontains=search) |
            Q(phone__icontains=search) |
            Q(pan_number__icontains=search)
        )
    return qs.order_by('name')


def list_inventory_categories(search: str = None):
    qs = InventoryCategory.objects.all()
    if search:
        qs = qs.filter(name__icontains=search.strip())
    return qs.order_by('name')


def list_inventory_for_branch_with_metrics(
    branch_id: int,
    search: str = None,
    category_id: int = None,
    category_name: str = None,
    low_stock_only: bool = False,
):
    """
    Returns full catalog query along with summary metrics (count, low_stock_count, total_valuation).
    """
    base_qs = (
        InventoryItem.objects
        .select_related('category', 'branch', 'supplier')
        .filter(branch_id=branch_id, is_active=True)
    )

    if search:
        s = search.strip()
        base_qs = base_qs.filter(
            Q(name__icontains=s) |
            Q(sku__icontains=s) |
            Q(supplier_name__icontains=s)
        )

    if category_id:
        base_qs = base_qs.filter(category_id=category_id)
    elif category_name:
        base_qs = base_qs.filter(category__name__iexact=category_name.strip())

    if low_stock_only:
        base_qs = base_qs.filter(current_stock__lte=F('min_threshold'))

    # Calculate aggregate metrics for this branch's active inventory
    agg_qs = InventoryItem.objects.filter(branch_id=branch_id, is_active=True)
    total_count = agg_qs.count()
    low_stock_count = agg_qs.filter(current_stock__lte=F('min_threshold')).count()
    
    val_result = agg_qs.aggregate(
        total_val=Sum(F('current_stock') * F('cost_per_unit'), output_field=DecimalField(max_digits=14, decimal_places=2))
    )['total_val']
    total_valuation = val_result if val_result is not None else Decimal('0.00')

    return {
        'count': total_count,
        'low_stock_count': low_stock_count,
        'total_valuation': total_valuation,
        'queryset': base_qs.order_by('name'),
    }


def list_inventory_for_branch(
    branch_id: int,
    category_id: int = None,
    low_stock_only: bool = False,
):
    qs = (
        InventoryItem.objects
        .select_related('category', 'branch', 'supplier')
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
        .select_related('category', 'branch', 'supplier')
        .filter(id=item_id)
        .first()
    )


def get_inventory_item_by_sku(branch_id: int, sku: str) -> InventoryItem:
    return (
        InventoryItem.objects
        .select_related('category', 'branch', 'supplier')
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


def list_purchase_invoices(branch_id: int, search: str = None, supplier_id: str = None, limit: int = 100):
    qs = (
        PurchaseInvoice.objects
        .select_related('supplier', 'branch', 'received_by')
        .prefetch_related('items', 'items__item')
        .filter(branch_id=branch_id)
    )
    if search:
        s = search.strip()
        qs = qs.filter(
            Q(invoice_number__icontains=s) |
            Q(supplier_name__icontains=s) |
            Q(notes__icontains=s)
        )
    if supplier_id:
        qs = qs.filter(supplier_id=supplier_id)
    return qs.order_by('-purchase_date', '-created_at')[:limit]


def get_purchase_invoice_by_id(purchase_id: str, branch_id: int = None):
    qs = (
        PurchaseInvoice.objects
        .select_related('supplier', 'branch', 'received_by')
        .prefetch_related('items', 'items__item')
        .filter(id=purchase_id)
    )
    if branch_id:
        qs = qs.filter(branch_id=branch_id)
    return qs.first()


def list_stock_movements(branch_id: int, item_id: int = None, limit: int = 100):
    qs = (
        StockMovementLedger.objects
        .select_related('item', 'recorded_by', 'branch')
        .filter(branch_id=branch_id)
    )
    if item_id:
        qs = qs.filter(item_id=item_id)
    return qs.order_by('-timestamp')[:limit]
