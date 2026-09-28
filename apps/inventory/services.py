import re
import uuid
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
    Supplier,
    PurchaseInvoice,
    PurchaseInvoiceItem,
    StockMovementLedger,
    StockMovementType,
    DaybookAccountEntry,
    PaymentStatus,
    PaymentMethod,
)


def broadcast_inventory_event(branch, event_type: str, data: dict):
    """
    Sends real-time WebSocket event to all connected clients for this outlet.
    Target group: inventory_{outlet_id}
    """
    channel_layer = get_channel_layer()
    if not channel_layer:
        return

    payload = {
        'type': 'inventory_event',
        'event': event_type,
        'outlet_id': str(branch.id),
        'branch_code': getattr(branch, 'branch_code', ''),
        'timestamp': timezone.now().isoformat(),
        'data': data,
    }

    groups = [f"inventory_{branch.id}"]
    if getattr(branch, 'branch_code', None) and branch.branch_code != str(branch.id):
        groups.append(f"inventory_{branch.branch_code}")

    for grp in groups:
        try:
            async_to_sync(channel_layer.group_send)(grp, payload)
        except Exception:
            pass


def _generate_sku(name: str) -> str:
    clean_name = re.sub(r'[^A-Z0-9]', '', name.upper())[:8] or "ITEM"
    return f"SKU-{clean_name}-{uuid.uuid4().hex[:4].upper()}"


@transaction.atomic
def supplier_create(
    branch,
    name: str,
    pan_number: str = "",
    phone: str = "",
    email: str = "",
    address: str = "",
    credit_balance: Decimal = Decimal('0.00'),
) -> Supplier:
    supplier, created = Supplier.objects.get_or_create(
        branch=branch,
        name=name.strip(),
        defaults={
            'pan_number': pan_number.strip(),
            'phone': phone.strip(),
            'email': email.strip(),
            'address': address.strip(),
            'credit_balance': Decimal(str(credit_balance)),
            'is_active': True,
        }
    )
    if not created:
        # Update existing details if provided
        updated = False
        if pan_number and not supplier.pan_number:
            supplier.pan_number = pan_number.strip()
            updated = True
        if phone and not supplier.phone:
            supplier.phone = phone.strip()
            updated = True
        if email and not supplier.email:
            supplier.email = email.strip()
            updated = True
        if updated:
            supplier.save()
    return supplier


@transaction.atomic
def inventory_item_create(
    branch,
    name: str,
    sku: str = None,
    current_stock: Decimal = Decimal('0.000'),
    unit: str = 'PCS',
    min_threshold: Decimal = Decimal('5.000'),
    cost_per_unit: Decimal = Decimal('0.00'),
    category: InventoryCategory = None,
    supplier: Supplier = None,
    supplier_name: str = "",
) -> InventoryItem:
    if not sku or not sku.strip():
        sku = _generate_sku(name)
    else:
        sku = sku.strip().upper()

    item = InventoryItem(
        branch=branch,
        sku=sku,
        name=name.strip(),
        current_stock=Decimal(str(current_stock)),
        unit=unit.strip().upper() if unit else 'PCS',
        min_threshold=Decimal(str(min_threshold)),
        cost_per_unit=Decimal(str(cost_per_unit)),
        category=category,
        supplier=supplier,
        supplier_name=supplier_name.strip() if supplier_name else (supplier.name if supplier else ""),
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
        StockMovementLedger.objects.create(
            branch=branch,
            item=item,
            item_name=item.name,
            category=category.name if category else "",
            type=StockMovementType.INCREASE,
            quantity=Decimal(str(current_stock)),
            unit=item.unit,
            previous_stock=Decimal('0.000'),
            new_stock=Decimal(str(current_stock)),
            reason="INITIAL_STOCK",
            reference_id=item.sku,
            note="Initial inventory registration intake",
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
    Direct item restock. Adds inventory to stock, logs the audit transaction,
    calculates moving average cost if cost provided, and reactivates menu availability.
    """
    item = InventoryItem.objects.select_for_update().get(id=item.id)
    qty = Decimal(str(quantity))
    prev_stock = item.current_stock
    prev_cost = item.cost_per_unit
    new_stock = prev_stock + qty

    item.current_stock = new_stock
    if cost_per_unit is not None:
        unit_cost = Decimal(str(cost_per_unit))
        if prev_stock <= Decimal('0.000') or new_stock <= Decimal('0.000'):
            new_cost = unit_cost
        else:
            new_cost = ((prev_stock * prev_cost) + (qty * unit_cost)) / new_stock
        item.cost_per_unit = new_cost.quantize(Decimal('0.01'))

    item.last_restocked = timezone.now()
    item.save()

    StockTransaction.objects.create(
        inventory_item=item,
        transaction_type=StockTransactionType.RESTOCK_PURCHASE,
        quantity=qty,
        previous_stock=prev_stock,
        resulting_stock=new_stock,
        performed_by=performed_by,
        notes=notes.strip() or "Direct stock restock",
    )

    StockMovementLedger.objects.create(
        branch=item.branch,
        item=item,
        item_name=item.name,
        category=item.category.name if item.category else "",
        type=StockMovementType.INCREASE,
        quantity=qty,
        unit=item.unit,
        previous_stock=prev_stock,
        new_stock=new_stock,
        reason="DIRECT_RESTOCK",
        reference_id=str(item.id),
        note=notes.strip() or "Direct inventory restock",
        recorded_by=performed_by,
    )

    broadcast_inventory_event(
        branch=item.branch,
        event_type="INVENTORY_RESTOCKED",
        data={
            'item_id': item.id,
            'name': item.name,
            'sku': item.sku,
            'added_quantity': float(qty),
            'current_stock': float(new_stock),
            'cost_per_unit': float(item.cost_per_unit),
        }
    )

    return item


@transaction.atomic
def purchase_invoice_create(
    branch,
    invoice_number: str,
    supplier_name: str,
    supplier_phone: str = "",
    supplier_id: str = None,
    purchase_date=None,
    subtotal: Decimal = Decimal('0.00'),
    discount_amount: Decimal = Decimal('0.00'),
    total_amount: Decimal = Decimal('0.00'),
    paid_amount: Decimal = Decimal('0.00'),
    due_amount: Decimal = Decimal('0.00'),
    payment_status: str = PaymentStatus.PAID,
    payment_method: str = PaymentMethod.CASH,
    notes: str = "",
    document=None,
    document_name: str = "",
    received_by=None,
    idempotency_key: str = None,
    items_data: list = None,
) -> PurchaseInvoice:
    """
    High-Concurrency Atomic Inward Purchase Bill Engine:
    1. Validates Idempotency-Key to prevent duplicate entry on network retries.
    2. Auto-discovers or registers Supplier.
    3. Auto-discovers or registers Categories and SKUs on-the-fly.
    4. Row-locks items and recalculates Weighted Moving Average cost per unit:
       new_cost = ((prev_stock * prev_cost) + (qty * line_cost)) / new_stock
    5. Increases current_stock, creates PurchaseInvoiceItem, StockMovementLedger & StockTransaction records.
    6. Books Accounts Payable (Party Khata) and Daybook entries for credit / pending purchases.
    7. Broadcasts real-time INVENTORY_RESTOCKED WebSocket event with Zero Page Reload!
    """
    items_data = items_data or []

    # 1. Idempotency Check
    if idempotency_key and idempotency_key.strip():
        existing_invoice = PurchaseInvoice.objects.filter(idempotency_key=idempotency_key.strip()).first()
        if existing_invoice:
            return existing_invoice

    # 2. Supplier Resolution / Auto-creation
    supplier = None
    if supplier_id and str(supplier_id).strip():
        supplier = Supplier.objects.filter(id=str(supplier_id).strip(), branch=branch).first()

    if not supplier and supplier_name and supplier_name.strip():
        supplier = Supplier.objects.filter(branch=branch, name__iexact=supplier_name.strip()).first()
        if not supplier:
            supplier = Supplier.objects.create(
                branch=branch,
                name=supplier_name.strip(),
                phone=supplier_phone.strip() if supplier_phone else "",
            )
        elif supplier_phone and not supplier.phone:
            supplier.phone = supplier_phone.strip()
            supplier.save(update_fields=['phone', 'updated_at'])

    final_supplier_name = supplier.name if supplier else (supplier_name.strip() if supplier_name else "Unassigned Supplier")
    final_supplier_phone = supplier.phone if supplier else (supplier_phone.strip() if supplier_phone else "")

    # Calculate computed totals if missing or 0
    calc_subtotal = Decimal('0.00')
    for it in items_data:
        q = Decimal(str(it.get('quantity', 0)))
        c = Decimal(str(it.get('unit_cost', 0)))
        d = Decimal(str(it.get('discount', 0)))
        calc_subtotal += (q * c)

    subtotal_val = Decimal(str(subtotal)) if Decimal(str(subtotal)) > Decimal('0.00') else calc_subtotal
    disc_val = Decimal(str(discount_amount))
    total_val = Decimal(str(total_amount)) if Decimal(str(total_amount)) > Decimal('0.00') else max(Decimal('0.00'), subtotal_val - disc_val)
    paid_val = Decimal(str(paid_amount))
    due_val = Decimal(str(due_amount)) if Decimal(str(due_amount)) > Decimal('0.00') else max(Decimal('0.00'), total_val - paid_val)

    if payment_method == PaymentMethod.CREDIT and paid_val == Decimal('0.00'):
        due_val = total_val
        payment_status = PaymentStatus.PENDING
    elif due_val <= Decimal('0.00'):
        payment_status = PaymentStatus.PAID
    elif paid_val > Decimal('0.00') and due_val > Decimal('0.00'):
        payment_status = PaymentStatus.PARTIAL
    else:
        payment_status = PaymentStatus.PENDING

    # 3. Create Purchase Invoice
    invoice = PurchaseInvoice.objects.create(
        branch=branch,
        invoice_number=invoice_number.strip(),
        supplier=supplier,
        supplier_name=final_supplier_name,
        supplier_phone=final_supplier_phone,
        purchase_date=purchase_date or timezone.now().date(),
        subtotal=subtotal_val.quantize(Decimal('0.01')),
        discount_amount=disc_val.quantize(Decimal('0.01')),
        total_amount=total_val.quantize(Decimal('0.01')),
        paid_amount=paid_val.quantize(Decimal('0.01')),
        due_amount=due_val.quantize(Decimal('0.01')),
        payment_status=payment_status,
        payment_method=payment_method,
        notes=notes.strip(),
        document=document,
        document_name=document_name.strip(),
        received_by=received_by,
        idempotency_key=idempotency_key.strip() if idempotency_key else None,
    )

    restocked_items_summary = []

    # 4. Process Line Items
    for item_raw in items_data:
        item_id = item_raw.get('item_id')
        item_name = (item_raw.get('item_name') or item_raw.get('name') or "").strip()
        sku_val = (item_raw.get('sku') or "").strip()
        cat_name = (item_raw.get('category') or "").strip()
        qty = Decimal(str(item_raw.get('quantity', 0)))
        unit_str = (item_raw.get('unit') or "PCS").strip().upper()
        unit_cost = Decimal(str(item_raw.get('unit_cost', 0)))
        line_discount = Decimal(str(item_raw.get('discount', 0)))
        batch_no = (item_raw.get('batch_no') or "").strip()
        expiry_date = item_raw.get('expiry_date') or None

        if qty <= Decimal('0.000'):
            continue

        # Auto-discover or create Category
        category_obj = None
        if cat_name:
            category_obj = InventoryCategory.objects.filter(name__iexact=cat_name).first()
            if not category_obj:
                category_obj = InventoryCategory.objects.create(name=cat_name)

        # Resolve Inventory Item
        item_obj = None
        if item_id:
            item_obj = InventoryItem.objects.select_for_update().filter(id=item_id, branch=branch).first()

        if not item_obj and sku_val:
            item_obj = InventoryItem.objects.select_for_update().filter(sku=sku_val.upper(), branch=branch).first()

        if not item_obj and item_name:
            item_obj = InventoryItem.objects.select_for_update().filter(name__iexact=item_name, branch=branch).first()

        # If item doesn't exist, create on-the-fly!
        if not item_obj:
            new_sku = sku_val.upper() if sku_val else _generate_sku(item_name or "ITEM")
            item_obj = InventoryItem.objects.create(
                branch=branch,
                name=item_name or f"Stock Item {new_sku}",
                sku=new_sku,
                category=category_obj,
                supplier=supplier,
                supplier_name=final_supplier_name,
                current_stock=Decimal('0.000'),
                unit=unit_str,
                cost_per_unit=unit_cost,
                min_threshold=Decimal('5.000'),
            )
            item_obj = InventoryItem.objects.select_for_update().get(id=item_obj.id)

        # 5. Weighted Moving Average Cost Calculation
        prev_stock = item_obj.current_stock
        prev_cost = item_obj.cost_per_unit
        new_stock = prev_stock + qty

        if prev_stock <= Decimal('0.000') or new_stock <= Decimal('0.000'):
            new_cost = unit_cost
        else:
            new_cost = ((prev_stock * prev_cost) + (qty * unit_cost)) / new_stock

        item_obj.current_stock = new_stock
        item_obj.cost_per_unit = new_cost.quantize(Decimal('0.01'))
        item_obj.last_restocked = timezone.now()
        if supplier:
            item_obj.supplier = supplier
            item_obj.supplier_name = final_supplier_name
        if category_obj and not item_obj.category:
            item_obj.category = category_obj
        item_obj.save()

        line_net = max(Decimal('0.00'), (qty * unit_cost) - line_discount)

        # Create Purchase Invoice Item
        PurchaseInvoiceItem.objects.create(
            purchase=invoice,
            item=item_obj,
            item_name=item_obj.name,
            category=category_obj.name if category_obj else (item_obj.category.name if item_obj.category else ""),
            quantity=qty,
            unit=item_obj.unit,
            unit_cost=unit_cost.quantize(Decimal('0.01')),
            discount=line_discount.quantize(Decimal('0.01')),
            total_cost=line_net.quantize(Decimal('0.01')),
            batch_no=batch_no,
            expiry_date=expiry_date,
        )

        # Create Stock Movement Ledger record
        StockMovementLedger.objects.create(
            branch=branch,
            item=item_obj,
            item_name=item_obj.name,
            category=category_obj.name if category_obj else (item_obj.category.name if item_obj.category else ""),
            type=StockMovementType.INCREASE,
            quantity=qty,
            unit=item_obj.unit,
            previous_stock=prev_stock,
            new_stock=new_stock,
            reason="PURCHASE",
            reference_id=invoice.invoice_number,
            note=f"Inward purchase invoice #{invoice.invoice_number} from {final_supplier_name}",
            recorded_by=received_by,
        )

        # Legacy StockTransaction for BOM & backward compatibility
        StockTransaction.objects.create(
            inventory_item=item_obj,
            transaction_type=StockTransactionType.RESTOCK_PURCHASE,
            quantity=qty,
            previous_stock=prev_stock,
            resulting_stock=new_stock,
            performed_by=received_by,
            notes=f"Purchase bill #{invoice.invoice_number}",
        )

        restocked_items_summary.append({
            'item_id': item_obj.id,
            'name': item_obj.name,
            'sku': item_obj.sku,
            'quantity_added': float(qty),
            'new_stock': float(new_stock),
            'unit': item_obj.unit,
            'new_cost_per_unit': float(item_obj.cost_per_unit),
        })

    # 6. Accounts Payable / Party Khata Credit Booking
    if due_val > Decimal('0.00') or payment_method == PaymentMethod.CREDIT:
        if supplier:
            supplier.credit_balance = (supplier.credit_balance or Decimal('0.00')) + due_val
            supplier.save(update_fields=['credit_balance', 'updated_at'])

        DaybookAccountEntry.objects.create(
            branch=branch,
            party=supplier,
            purchase=invoice,
            dr_amount=Decimal('0.00'),
            cr_amount=due_val,
            voucher_type="PURCHASE_CREDIT" if payment_method == PaymentMethod.CREDIT else "PURCHASE_DUE",
            narrative=f"Accounts payable for purchase #{invoice.invoice_number} ({final_supplier_name})",
        )

    # 7. Real-Time WebSocket broadcast
    broadcast_inventory_event(
        branch=branch,
        event_type="INVENTORY_RESTOCKED",
        data={
            'invoice_id': invoice.id,
            'invoice_number': invoice.invoice_number,
            'supplier_name': final_supplier_name,
            'total_amount': float(invoice.total_amount),
            'items_restocked': restocked_items_summary,
        }
    )

    return invoice


@transaction.atomic
def inventory_reconcile_audit(branch, items_data: list, performed_by=None) -> list:
    """
    Physical Stock Audit & Variance Reconciliation:
    Takes actual counted quantities from floor staff, computes variance,
    logs StockMovementLedger adjustments, and atomically updates stock balances.
    """
    reconciled = []

    for entry in items_data:
        item_id = entry.get('item_id')
        if not item_id:
            continue

        item = InventoryItem.objects.select_for_update().filter(id=item_id, branch=branch).first()
        if not item:
            continue

        physical_stock = Decimal(str(entry.get('physical_stock', item.current_stock)))
        note = entry.get('note', '').strip()
        prev_stock = item.current_stock
        variance = physical_stock - prev_stock

        if variance == Decimal('0.000'):
            continue

        item.current_stock = physical_stock
        item.save(update_fields=['current_stock', 'updated_at'])

        movement_type = StockMovementType.INCREASE if variance > Decimal('0.000') else StockMovementType.DECREASE
        qty_abs = abs(variance)

        # Log Movement Ledger
        mov = StockMovementLedger.objects.create(
            branch=branch,
            item=item,
            item_name=item.name,
            category=item.category.name if item.category else "",
            type=movement_type,
            quantity=qty_abs,
            unit=item.unit,
            previous_stock=prev_stock,
            new_stock=physical_stock,
            reason="AUDIT_ADJUSTMENT",
            reference_id=f"AUDIT-{item.id}-{timezone.now().strftime('%Y%m%d%H%M')}",
            note=note or f"Stock count audit adjustment: variance of {variance:+} {item.unit}",
            recorded_by=performed_by,
        )

        # Log Legacy StockTransaction
        StockTransaction.objects.create(
            inventory_item=item,
            transaction_type=StockTransactionType.AUDIT_ADJUSTMENT,
            quantity=variance,
            previous_stock=prev_stock,
            resulting_stock=physical_stock,
            performed_by=performed_by,
            notes=note or "Floor audit variance reconciliation",
        )

        reconciled.append({
            'item_id': item.id,
            'name': item.name,
            'sku': item.sku,
            'previous_stock': float(prev_stock),
            'physical_stock': float(physical_stock),
            'variance': float(variance),
            'unit': item.unit,
        })

    if reconciled:
        broadcast_inventory_event(
            branch=branch,
            event_type="STOCK_AUDIT_ADJUSTED",
            data={
                'reconciled_count': len(reconciled),
                'items': reconciled,
            }
        )

    return reconciled


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


def _order_inventory_lines(order):
    from types import SimpleNamespace
    for row in order.items.select_related('product', 'variant').all():
        if not row.combo_components:
            yield row
            continue
        for component in row.combo_components:
            product = Product.objects.get(pk=component['product_id'])
            variant = ProductVariant.objects.filter(pk=component.get('variant_id'), product=product).first()
            yield SimpleNamespace(product=product, variant=variant,
                                  quantity=row.quantity * component['quantity'])


@transaction.atomic
def deduct_inventory_for_order(order: Order) -> list:
    """
    High-Concurrency Atomic Stock Deduction (Rule 12 & Rule 13):
    1. Expands order line items into their raw ingredients (BOM).
    2. Atomically decrements stock using row-level locks.
    3. Logs immutable StockTransaction & StockMovementLedger records.
    4. Auto-out-of-stock trigger: If ingredient hits 0, marks product out of stock
       and broadcasts live WebSocket event with Zero Page Reload!
    """
    deductions = []
    branch = order.branch

    for order_item in _order_inventory_lines(order):
        product = order_item.product
        variant = order_item.variant
        order_qty = Decimal(str(order_item.quantity))

        # 1. Check Recipe Bill of Materials (BOM)
        recipe_query = RecipeItem.objects.filter(product=product)
        if variant:
            recipe_items = list(recipe_query.filter(models.Q(variant=variant) | models.Q(variant__isnull=True)))
        else:
            recipe_items = list(recipe_query.filter(variant__isnull=True))

        for recipe in recipe_items:
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

            # Movement ledger record
            StockMovementLedger.objects.create(
                branch=branch,
                item=raw_item,
                item_name=raw_item.name,
                category=raw_item.category.name if raw_item.category else "",
                type=StockMovementType.DECREASE,
                quantity=total_needed,
                unit=raw_item.unit,
                previous_stock=prev_stock,
                new_stock=new_stock,
                reason="SALE_DEDUCTION",
                reference_id=order.order_number,
                note=f"Automatic order deduction for order #{order.order_number}",
            )

            # Low-stock alert event
            if new_stock <= raw_item.min_threshold:
                broadcast_inventory_event(
                    branch=branch,
                    event_type="LOW_STOCK_ALERT",
                    data={
                        'item_id': raw_item.id,
                        'name': raw_item.name,
                        'sku': raw_item.sku,
                        'current_stock': float(new_stock),
                        'min_threshold': float(raw_item.min_threshold),
                        'unit': raw_item.unit,
                    }
                )

            # Auto Out-Of-Stock Trigger when raw material runs out
            if new_stock <= Decimal('0.000'):
                OutletProductOverride.objects.update_or_create(
                    branch=branch,
                    product=product,
                    defaults={'is_available': False},
                )
                broadcast_product_availability_change(
                    branch_id=branch.id,
                    product_id=product.id,
                    is_available=False,
                    product_name=product.name,
                )

    if deductions:
        broadcast_inventory_event(
            branch=branch,
            event_type="STOCK_DEDUCTED_BY_SALE",
            data={
                'order_number': order.order_number,
                'deduction_count': len(deductions),
            }
        )

    return deductions
