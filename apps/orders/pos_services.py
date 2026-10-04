"""Atomic staff commands: price authority, concurrency, ledgers and durable events."""
import hashlib
import json
import uuid
from decimal import Decimal, ROUND_HALF_UP
from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from rest_framework.exceptions import APIException, ValidationError, NotFound
from apps.catalog.selectors import quote_items, product_queryset
from apps.restaurants.models import Branch
from apps.tables.models import DiningTable
from apps.payments.models import PaymentTransaction
from .models import Order, OrderItem, OrderItemModifier, OrderStatusHistory, PosSequence, PosMutation, PosReceipt, PosCreditEntry, OrderOutboxEvent
from .preparation import append_allowed, sync_status, advance_round, call_round
from .numbering import generate_order_number
from .pos_access import require_access
from .pos_selectors import order_queryset, order_data, ACTIVE


class Conflict(APIException):
    status_code = 409
    default_detail = 'This order changed. Review the latest version and retry.'


def money(value):
    return Decimal(value).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)


def pricing_policy(branch):
    restaurant = branch.restaurant
    return {'vat_enabled':restaurant.is_vat_enabled,'vat_rate':str(restaurant.vat_rate_percent),
            'service_enabled':restaurant.is_service_charge_enabled,'service_rate':str(restaurant.service_charge_percent)}


def totals(branch, subtotal, discount=0, method='CASH', policy=None):
    subtotal, discount = money(subtotal), money(discount)
    if discount < 0 or discount > subtotal:
        raise ValidationError('Discount must be between zero and the subtotal.')
    policy = policy or pricing_policy(branch)
    net = subtotal-discount
    charge = money(net*Decimal(policy['service_rate'])/100) if policy['service_enabled'] else Decimal('0.00')
    net += charge
    savings = net % 1 if method == 'CASH' else Decimal('0.00')
    total = net-savings+Decimal(policy.get('customer_tip', '0'))
    vat_rate = Decimal(policy['vat_rate'])
    vat = money((net-savings)*vat_rate/(100+vat_rate)) if policy['vat_enabled'] else Decimal('0.00')
    if total > Decimal('9999999999.99'):
        raise ValidationError('Order exceeds the supported monetary range.')
    return {k: str(v) for k,v in dict(subtotal=subtotal, discount_amount=discount, service_charge_amount=charge,
        cash_round_down_savings=savings, vat_included_amount=vat, total_payable=total).items()}


def priced_totals(branch, subtotal, phone='', manual=0, method='CASH', policy=None, order_id=None, frozen=False):
    from apps.loyalty.services import discount
    policy = policy or pricing_policy(branch)
    if frozen:
        effective, reward = Decimal(manual), policy.get('loyalty')
    else:
        effective, reward = discount(branch, phone, subtotal, manual, order_id)
    return {**totals(branch, subtotal, effective, method, policy), 'loyalty': reward,
            'manual_discount_amount': str(manual)}


def quote(branch, data):
    result = quote_items(branch, data['items'], 'pos')
    result.update(priced_totals(branch, result['subtotal'], data.get('customer_phone', ''),
        data.get('discount_amount', 0), data.get('payment_method', 'CASH')))
    return result


def order_totals(order, manual=None, phone=None, subtotal=None):
    frozen = bool(order.billed_at or order.paid_amount or order.credit_amount or order.order_source == 'WEBSITE')
    if frozen:
        manual = order.discount_amount
    elif manual is None:
        manual = Decimal(order.pricing_policy.get('manual_discount_amount', str(order.discount_amount)))
    return priced_totals(order.branch, subtotal if subtotal is not None else order.subtotal,
        phone if phone is not None else order.customer_phone, manual, order.payment_method,
        order.pricing_policy, order.pk, frozen)


def apply_totals(order, manual=None):
    result = order_totals(order, manual)
    for key, value in result.items():
        if key not in ('loyalty', 'manual_discount_amount'):
            setattr(order, key, Decimal(value))
    if not (order.billed_at or order.paid_amount or order.credit_amount or order.order_source == 'WEBSITE'):
        order.pricing_policy = {**order.pricing_policy, 'loyalty': result['loyalty'],
                                'manual_discount_amount': result['manual_discount_amount']}
        if result['loyalty']:
            order.discount_reason = f"Loyalty: {result['loyalty']['name']} ({result['loyalty']['percent']}%)"
        elif order.discount_reason.startswith('Loyalty:'):
            order.discount_reason = ''
    order.payment_status = 'PAID' if order.paid_amount >= order.total_payable else 'UNPAID'


def consume_stock(order, row, actor):
    from collections import defaultdict
    from apps.inventory.models import RecipeItem, InventoryItem, StockTransaction, StockMovementLedger
    needs = defaultdict(Decimal)
    components = row.combo_components or [{'product_id': row.product_id, 'variant_id': row.variant_id, 'quantity': 1}]
    for component in components:
        recipes = RecipeItem.objects.filter(product_id=component['product_id'], inventory_item__branch=order.branch)
        for recipe in recipes:
            if recipe.variant_id is None or recipe.variant_id == component.get('variant_id'):
                needs[recipe.inventory_item_id] += recipe.quantity_required*row.quantity*component['quantity']
    snapshots = []
    for item in InventoryItem.objects.select_for_update().filter(pk__in=needs).order_by('pk'):
        amount = needs[item.pk]
        if item.current_stock < amount:
            raise Conflict(f'Insufficient stock for {row.product_name}: {item.name}.')
        previous = item.current_stock
        item.current_stock -= amount
        item.save(update_fields=['current_stock','updated_at'])
        StockTransaction.objects.create(inventory_item=item, transaction_type='ORDER_DEDUCTION', quantity=-amount,
            previous_stock=previous, resulting_stock=item.current_stock, reference_order=order, performed_by=actor,
            notes=f'POS line {row.pk}, round {row.round_number}')
        StockMovementLedger.objects.create(branch=order.branch,item=item,item_name=item.name,type='DECREASE',
            quantity=amount,unit=item.unit,previous_stock=previous,new_stock=item.current_stock,reason='SALE_DEDUCTION',
            reference_id=order.order_number,note=f'POS line {row.pk}',recorded_by=actor)
        snapshots.append({'item_id': item.pk, 'quantity': str(amount)})
    row.stock_consumption = snapshots
    row.save(update_fields=['stock_consumption'])


def restore_stock(order, row, actor, quantity=None):
    from apps.inventory.models import InventoryItem, StockTransaction, StockMovementLedger
    if row.kitchen_status != 'WAITING':
        return
    fraction = Decimal(quantity or row.quantity) / row.quantity
    needs = {r['item_id']: Decimal(r['quantity'])*fraction for r in row.stock_consumption}
    for item in InventoryItem.objects.select_for_update().filter(pk__in=needs, branch=order.branch).order_by('pk'):
        previous = item.current_stock
        item.current_stock += needs[item.pk]
        item.save(update_fields=['current_stock','updated_at'])
        StockTransaction.objects.create(inventory_item=item, transaction_type='AUDIT_ADJUSTMENT', quantity=needs[item.pk],
            previous_stock=previous, resulting_stock=item.current_stock, reference_order=order, performed_by=actor,
            notes=f'POS unprepared line {row.pk} voided')
        StockMovementLedger.objects.create(branch=order.branch,item=item,item_name=item.name,type='INCREASE',
            quantity=needs[item.pk],unit=item.unit,previous_stock=previous,new_stock=item.current_stock,reason='POS_VOID',
            reference_id=order.order_number,note=f'POS line {row.pk}',recorded_by=actor)


def add_lines(order, raw_items, priced, actor):
    products = {p.pk:p for p in product_queryset().filter(pk__in=[r['product_id'] for r in priced['items']])}
    round_number = (order.items.aggregate(n=Max('round_number'))['n'] or 0)+1
    for raw, line in zip(raw_items, priced['items']):
        product = products[line['product_id']]
        variant = next((v for v in product.variants.all() if v.pk == line['variant_id']),None)
        components = line['combo_components']
        row = OrderItem.objects.create(order=order, product=product, product_name=product.name, variant=variant,
            variant_name=variant.name if variant else '', quantity=line['quantity'], unit_price=line['unit_price'],
            line_total=line['line_total'], combo_components=components, round_number=round_number,
            requires_kitchen=any(c['requires_kitchen'] for c in components) if components else product.requires_kitchen,
            item_notes=raw.get('item_notes',''))
        selected = set(raw.get('modifier_option_ids',[]))
        OrderItemModifier.objects.bulk_create([OrderItemModifier(order_item=row,group_name=g.name,option_name=o.name,price_delta=o.price_delta)
            for g in product.modifier_groups.all() for o in g.options.all() if o.pk in selected])
        consume_stock(order,row,actor)
    if not order.items.filter(round_number=round_number, requires_kitchen=True).exists():
        order.items.filter(round_number=round_number).update(kitchen_status='READY', ready_at=timezone.now())
    return round_number


def remove_waiting_item(order, data, actor=None):
    if order.billed_at or order.paid_amount or order.credit_amount or order.status in ('OUT_FOR_DELIVERY','COMPLETED','CANCELLED'):
        raise ValidationError('Only unpaid, unbilled, unprepared items can be reduced or removed.')
    if order.order_source == 'WEBSITE' and order.status != 'PENDING':
        raise ValidationError('Confirmed web orders cannot be edited.')
    row = order.items.filter(pk=data['item_id'], is_voided=False).first()
    if not row or row.kitchen_status != 'WAITING' or row.preparation_started_at:
        raise ValidationError('Cooking, ready and served items cannot be reduced or removed.')
    quantity = data.get('quantity', row.quantity)
    if quantity > row.quantity:
        raise ValidationError('Removal quantity exceeds this line quantity.')
    if quantity == row.quantity and order.items.filter(is_voided=False).count() <= 1:
        raise ValidationError('Cancel the order to remove its final item.')
    reduction = row.line_total * Decimal(quantity) / row.quantity
    restore_stock(order, row, actor, quantity)
    if quantity == row.quantity:
        row.is_voided = True
    else:
        fraction = Decimal(row.quantity-quantity)/row.quantity
        row.stock_consumption = [{**value,'quantity':str(Decimal(value['quantity'])*fraction)} for value in row.stock_consumption]
        row.quantity -= quantity
        row.line_total -= reduction
    row.void_reason = data['reason']
    row.save()
    remaining = order.items.filter(round_number=row.round_number, is_voided=False)
    if not remaining.filter(requires_kitchen=True).exists():
        remaining.filter(kitchen_status='WAITING').update(kitchen_status='READY', ready_at=timezone.now())
    order.subtotal -= reduction
    apply_totals(order)
    if order.status != 'PENDING':
        sync_status(order)


def record_tenders(order, tenders, actor):
    if not tenders: return
    require_access(actor,order.branch,'billing')
    due = order.total_payable-order.paid_amount
    incoming = sum((r['amount'] for r in tenders),Decimal(0))
    new_credit = sum((r['amount'] for r in tenders if r['method']=='CREDIT'),Decimal(0))
    paid = incoming-new_credit
    # Existing credit is cleared by collected payments, not duplicated by new credit allocation.
    if incoming > due or new_credit > max(Decimal(0),due-order.credit_amount-paid):
        raise ValidationError('Tender exceeds the remaining balance. Enter the amount applied, excluding cash change.')
    if new_credit and (not order.customer_phone.strip() or order.customer_name in ('','Walk-in Guest','Guest')):
        raise ValidationError('Khata requires the customer name and phone number.')
    for row in tenders:
        method = row['method']
        enabled = {'CASH':'enable_cash','CARD':'enable_card','FONEPAY':'enable_fonepay','ESEWA':'enable_esewa','KHALTI':'enable_khalti'}.get(method)
        if enabled and not getattr(order.branch.restaurant,enabled):
            raise ValidationError(f'{method} is disabled for this restaurant.')
        if method == 'CREDIT':
            PosCreditEntry.objects.create(order=order,amount=row['amount'],customer_phone=order.customer_phone,reason='Staff credit allocation',actor=actor)
        else:
            PaymentTransaction.objects.create(order=order,branch=order.branch,transaction_id=f'POS-{uuid.uuid4().hex}',
                amount=row['amount'],payment_method=method,status='SUCCESS',gateway_ref=row.get('reference',''),received_by=actor)
    cleared = min(paid,order.credit_amount)
    if cleared:
        PosCreditEntry.objects.create(order=order,amount=-cleared,customer_phone=order.customer_phone,reason='Credit collected',actor=actor)
    order.credit_amount += new_credit-cleared
    order.paid_amount += paid
    order.payment_status = 'PAID' if order.paid_amount >= order.total_payable else 'UNPAID'
    order.billed_at = timezone.now()


def receipt(order, kind, sequence):
    sequence.receipt_counter += 1
    sequence.save(update_fields=['receipt_counter'])
    snapshot = order_data(order_queryset(order.branch).get(pk=order.pk))
    restaurant = order.branch.restaurant
    snapshot['seller'] = {'name':restaurant.legal_name or restaurant.name,'outlet':order.branch.name,
        'address':order.branch.address_line,'phone':order.branch.phone_number,'pan':restaurant.pan_number,
        'fiscal_year':restaurant.fiscal_year,'currency':restaurant.currency}
    return PosReceipt.objects.create(order=order,number=f'{kind}-{order.branch_id}-{sequence.receipt_counter:08d}',kind=kind,snapshot=snapshot)


def audit(order, actor, previous, note):
    OrderStatusHistory.objects.create(order=order,from_status=previous,to_status=order.status,changed_by=actor,notes=note[:255])


@transaction.atomic
def mutate(branch, actor, key, action, data, order_id=None):
    if not key or len(key)>128:
        raise ValidationError('A valid Idempotency-Key header is required.')
    capability = {'settle':'billing','bill':'billing','refund':'refund','void':'discount','transition':'kitchen','call':'kitchen','round':'kitchen'}.get(action,'orders')
    require_access(actor,branch,capability)
    fingerprint = hashlib.sha256(json.dumps([actor.pk,action,order_id,data],sort_keys=True,default=str).encode()).hexdigest()
    # Short per-outlet write lock also serializes table allocation and receipt numbering.
    branch = Branch.objects.select_for_update().select_related('restaurant').get(pk=branch.pk)
    previous_mutation = PosMutation.objects.filter(branch=branch,key=key).first()
    if previous_mutation:
        if previous_mutation.fingerprint != fingerprint: raise Conflict('Idempotency key was already used for a different request.')
        return previous_mutation.response
    sequence,_ = PosSequence.objects.get_or_create(branch=branch)
    if action == 'create':
        if not branch.enable_pos or not branch.accepting_orders: raise ValidationError('This outlet is not accepting POS orders.')
        fulfillment = data['fulfillment_type']
        if not getattr(branch,{'DINE_IN':'enable_dine_in','TAKEAWAY':'enable_takeaway','DELIVERY':'enable_delivery','DRIVE_THRU':'enable_drive_thru'}[fulfillment]):
            raise ValidationError('This fulfillment mode is disabled at this outlet.')
        table = None
        if fulfillment == 'DINE_IN':
            table = DiningTable.objects.filter(pk=data.get('table_id'),branch=branch,is_active=True).first()
            if not table: raise ValidationError('Choose an active table at this outlet.')
            if Order.objects.filter(branch=branch,table=table,status__in=ACTIVE).exists():
                raise Conflict('This table already has an active order. Add a round to that order.')
        elif data.get('table_id'): raise ValidationError('Only dine-in orders can have a table.')
        if fulfillment == 'DELIVERY' and (not data['delivery_address'] or not data['customer_phone']):
            raise ValidationError('Delivery requires an address and phone number.')
        if data['discount_amount']:
            require_access(actor,branch,'discount')
            if not data['discount_reason']: raise ValidationError('Enter a discount reason.')
        priced = quote(branch,data)
        if Decimal(priced['total_payable']) != data['expected_total']: raise Conflict('Menu prices changed. Review a fresh quote.')
        order = Order.objects.create(branch=branch,is_pos_managed=True,order_source='POS',pricing_policy={**pricing_policy(branch), 'loyalty': priced['loyalty'], 'manual_discount_amount': str(data['discount_amount'])},
            order_number=generate_order_number('POS'),table=table,table_session_id=uuid.uuid4() if table else None,
            status='ACCEPTED',**{k:data[k] for k in ['customer_name','customer_phone','fulfillment_type','delivery_address','notes','payment_method','discount_reason']},
            **{k:Decimal(priced[k]) for k in ['subtotal','discount_amount','service_charge_amount','cash_round_down_savings','vat_included_amount','total_payable']})
        if priced.get('loyalty'):
            order.discount_reason = f"Loyalty: {priced['loyalty']['name']} ({priced['loyalty']['percent']}%)"
        add_lines(order,data['items'],priced,actor)
        if table:
            table.active_session_id = order.table_session_id
            table.save(update_fields=['active_session_id','updated_at'])
        if not order.items.filter(requires_kitchen=True).exists(): order.status='READY'
        record_tenders(order,data['tenders'],actor)
        previous = ''
    else:
        order = Order.objects.select_for_update(of=('self',)).select_related('branch__restaurant','table').filter(pk=order_id,branch=branch,is_pos_managed=True).first()
        if not order: raise NotFound('Order not found at this outlet.')
        if order.version != data['version']: raise Conflict()
        previous = order.status
        if action == 'append':
            if not append_allowed(order): raise ValidationError('New rounds are allowed only on open POS, kiosk or table orders before dispatch. Confirmed web orders cannot be edited.')
            if not branch.accepting_orders: raise ValidationError('This outlet is not accepting new rounds.')
            priced = quote_items(branch,data['items'],'pos')
            order.subtotal += Decimal(priced['subtotal'])
            apply_totals(order)
            if order.total_payable != data['expected_total']: raise Conflict('Order total changed. Review a fresh quote.')
            add_lines(order,data['items'],priced,actor)
            sync_status(order)
        elif action == 'round':
            advance_round(order, data['round_number'], data['status'])
        elif action == 'call':
            data['round_number'] = call_round(order, data.get('round_number'))
        elif action == 'settle':
            if order.status == 'CANCELLED': raise ValidationError('Cancelled orders cannot be settled.')
            frozen = bool(order.billed_at or order.paid_amount or order.credit_amount)
            old_manual = Decimal(order.pricing_policy.get('manual_discount_amount', str(order.discount_amount)))
            requested = data.get('discount_amount', old_manual)
            if frozen:
                if requested not in (old_manual, order.discount_amount):
                    raise ValidationError('A billed or partially paid order cannot be discounted again.')
            elif requested != old_manual:
                require_access(actor,branch,'discount')
                if not data['discount_reason']: raise ValidationError('Enter a discount reason.')
                order.discount_reason = data['discount_reason']
            for field in ['customer_name','customer_phone']:
                if field in data:
                    if frozen and data[field] != getattr(order,field):
                        raise ValidationError('Customer cannot be changed after billing or payment.')
                    setattr(order,field,data[field])
            apply_totals(order, None if frozen else requested)
            if order.paid_amount+order.credit_amount>order.total_payable:
                raise ValidationError('Discount exceeds the unsettled balance.')
            record_tenders(order,data['tenders'],actor)
        elif action == 'bill':
            if order.status == 'CANCELLED': raise ValidationError('A cancelled order cannot be billed.')
            apply_totals(order)
            order.billed_at = timezone.now()
        elif action == 'transition':
            target=data['status']
            allowed={'PENDING':['ACCEPTED','CANCELLED'],'ACCEPTED':['PREPARING','CANCELLED'], 'PREPARING':['READY','CANCELLED'],
                'READY':['COMPLETED','OUT_FOR_DELIVERY','CANCELLED'],'OUT_FOR_DELIVERY':['COMPLETED','CANCELLED']}
            if target not in allowed.get(order.status,[]): raise ValidationError('This status transition is not allowed.')
            if target=='OUT_FOR_DELIVERY' and order.fulfillment_type!='DELIVERY': raise ValidationError('Only delivery orders can be dispatched.')
            if target=='CANCELLED':
                if previous == 'OUT_FOR_DELIVERY' or order.items.filter(is_voided=False,kitchen_status='SERVED').exists() or order.items.filter(is_voided=False, requires_kitchen=True).exclude(kitchen_status='WAITING').exists():
                    raise ValidationError('Cooking has started. Remove only waiting items; this order cannot be cancelled.')
                require_access(actor,branch,'discount')
                if not data['reason']: raise ValidationError('Enter a cancellation reason.')
                if order.paid_amount > order.refunded_amount: raise ValidationError('Refund collected payments before cancelling this order.')
                if order.credit_amount:
                    PosCreditEntry.objects.create(order=order,amount=-order.credit_amount,customer_phone=order.customer_phone,reason=data['reason'],actor=actor)
                    order.credit_amount=0
                for row in order.items.filter(is_voided=False): restore_stock(order,row,actor)
            order.status=target
            if target=='PREPARING':
                order.items.filter(is_voided=False,kitchen_status='WAITING').update(kitchen_status='PREPARING',preparation_started_at=timezone.now())
                sync_status(order)
            if target=='READY':
                if not order.items.filter(is_voided=False,kitchen_status='PREPARING').exists():
                    raise ValidationError('Start a waiting round before marking it ready.')
                order.items.filter(is_voided=False,kitchen_status='PREPARING').update(kitchen_status='READY',ready_at=timezone.now())
                sync_status(order)
            if target in ('OUT_FOR_DELIVERY','COMPLETED'):
                if order.items.filter(is_voided=False,kitchen_status__in=['WAITING','PREPARING']).exists():
                    raise ValidationError('Every round must be ready before final handover or dispatch.')
                if target=='COMPLETED':
                    order.items.filter(is_voided=False).exclude(kitchen_status='SERVED').update(kitchen_status='SERVED',served_at=timezone.now())
            if target in ('COMPLETED','CANCELLED') and order.table_id:
                DiningTable.objects.filter(pk=order.table_id,active_session_id=order.table_session_id).update(active_session_id=None)
        elif action == 'void':
            remove_waiting_item(order, data, actor)
        elif action == 'refund':
            if data['amount']>order.paid_amount-order.refunded_amount: raise ValidationError('Refund exceeds collected payments.')
            PaymentTransaction.objects.create(order=order,branch=branch,transaction_id=f'REF-{uuid.uuid4().hex}',amount=data['amount'],
                payment_method=data['method'],status='REFUNDED',gateway_ref=data['reference'],received_by=actor,raw_response={'reason':data['reason']})
            order.refunded_amount+=data['amount']
            order.payment_status='REFUNDED'
        elif action != 'call': raise ValidationError('Unknown command.')
        order.version+=1
    order.save()
    audit(order,actor,previous, data.get('reason') or data.get('discount_reason') or (f'Round {data["round_number"]}: {data.get("status", action)}' if data.get('round_number') else f'Staff POS {action}'))
    if action in ['create','append','void']: receipt(order,'TOKEN',sequence)
    if action in ('settle','bill') or (action=='create' and data['tenders']): receipt(order,'BILL',sequence)
    if action=='refund': receipt(order,'REFUND',sequence)
    response=order_data(order_queryset(branch).get(pk=order.pk))
    if action in ('create','append','void') or (action=='transition' and order.status=='CANCELLED'):
        from apps.catalog.services import menu_changed
        menu_changed(branch_id=branch.pk)
    OrderOutboxEvent.objects.create(branch=branch,order=order,event_type=f'ORDER_{action.upper()}',payload={'version':order.version, 'round_number':data.get('round_number'), 'round_status':data.get('status') if action=='round' else None})
    PosMutation.objects.create(branch=branch,key=key,fingerprint=fingerprint,response=response)
    return response
