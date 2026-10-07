import hashlib
import time
from urllib.parse import parse_qs
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.core import signing
from apps.user_accounts.models import User
from .models import CustomerOrder


class AudienceRevisionConsumer(AsyncJsonWebsocketConsumer):
    """Public revision notifications only; all analytics and contacts require staff REST auth."""
    async def connect(self):
        self.outlet_id = self.scope['url_route']['kwargs']['outlet_id']
        self.group = f'customer_accounts_{self.outlet_id}'
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()

    async def account_event(self, event):
        await self.send_json({key: value for key, value in event.items() if key != 'type'})

    async def disconnect(self, code):
        if hasattr(self, 'group'):
            await self.channel_layer.group_discard(self.group, self.channel_name)

    @database_sync_to_async
    def revision(self):
        from django.db.models import Max, Count
        from apps.orders.models import Order
        from .models import WebsiteVisit, CustomerContact
        visits = WebsiteVisit.objects.filter(branch_id=self.outlet_id).aggregate(last=Max('created_at'))
        contacts = CustomerContact.objects.filter(branch_id=self.outlet_id).aggregate(last=Max('last_seen'))
        orders = Order.objects.filter(branch_id=self.outlet_id).aggregate(last=Max('updated_at'), count=Count('pk'))
        return hashlib.sha256(repr((visits, contacts, orders)).encode()).hexdigest()

    async def receive_json(self, content, **kwargs):
        if content.get('type') == 'ping':
            await self.send_json({'event_type':'HEARTBEAT','revision':await self.revision()})


class CustomerOrdersConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        try:
            ticket = parse_qs(self.scope['query_string'].decode()).get('ticket',[''])[0]
            self.user_id = signing.loads(ticket, salt='customer-socket', max_age=60)['user_id']
            if not await self.allowed():
                raise ValueError()
        except (ValueError, KeyError, signing.BadSignature):
            await self.close(code=4403); return
        self.group = f'customer_orders_{self.user_id}'
        self.started = time.monotonic()
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()

    @database_sync_to_async
    def allowed(self):
        return User.objects.filter(pk=self.user_id, is_active=True).exists()

    @database_sync_to_async
    def revision(self):
        rows = list(CustomerOrder.objects.filter(user_id=self.user_id).order_by('order_id').values_list('order_id','order__version','order__updated_at',
            'order__status', 'order__payment_status', 'order__paid_amount', 'order__credit_amount', 'order__refunded_amount',
            'order__delivery_dispatch__updated_at', 'order__delivery_dispatch__status'))
        return hashlib.sha256(repr(rows).encode()).hexdigest()

    async def receive_json(self, content, **kwargs):
        if time.monotonic()-self.started > 300 or not await self.allowed():
            await self.close(code=4001); return
        if content.get('type') == 'ping':
            await self.send_json({'type':'heartbeat','revision':await self.revision()})

    async def customer_event(self, event):
        if await self.allowed():
            await self.send_json({'type':'orders_changed'})
        else:
            await self.close(code=4403)

    async def disconnect(self, code):
        if hasattr(self,'group'):
            await self.channel_layer.group_discard(self.group, self.channel_name)
