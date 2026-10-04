import asyncio
from urllib.parse import parse_qs
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.contrib.auth import get_user_model
from django.core import signing
from django.db.models import Max
from apps.orders.pos_access import can_access
from apps.restaurants.models import Branch
from apps.payments.models import PaymentTransaction
from .models import DaybookEvent


class DaybookConsumer(AsyncJsonWebsocketConsumer):
    @database_sync_to_async
    def authorized(self):
        user = get_user_model().objects.filter(pk=self.user_id, is_active=True).first()
        branch = Branch.objects.filter(pk=self.branch_id, is_active=True, restaurant__is_active=True).first()
        return bool(branch and can_access(user, branch, 'daybook'))

    @database_sync_to_async
    def revision(self):
        event = DaybookEvent.objects.filter(branch_id=self.branch_id).aggregate(latest=Max('pk'))['latest'] or 0
        payment = PaymentTransaction.objects.filter(branch_id=self.branch_id).aggregate(latest=Max('updated_at'))['latest']
        return f'{event}:{payment.isoformat() if payment else ""}'

    async def connect(self):
        self.expiry_task = None
        self.group = None
        try:
            ticket = parse_qs(self.scope['query_string'].decode()).get('ticket', [''])[0]
            data = signing.loads(ticket, salt='daybook-socket', max_age=60)
            self.user_id = data['user_id']
            self.branch_id = int(self.scope['url_route']['kwargs']['outlet_id'])
            if data['branch_id'] != self.branch_id or not await self.authorized():
                raise ValueError()
        except (ValueError, KeyError, signing.BadSignature):
            await self.close(code=4403)
            return
        self.group = f'daybook_{self.branch_id}'
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()
        self.expiry_task = asyncio.create_task(self.expire())

    async def expire(self):
        await asyncio.sleep(300)
        await self.close(code=4001)

    async def receive_json(self, content, **kwargs):
        if not await self.authorized():
            await self.close(code=4403)
            return
        if content.get('type') == 'ping':
            await self.send_json({'event_type': 'HEARTBEAT', 'revision': await self.revision()})

    async def daybook_event(self, event):
        if not await self.authorized():
            await self.close(code=4403)
            return
        await self.send_json({key: value for key, value in event.items() if key != 'type'})

    async def disconnect(self, code):
        if self.expiry_task:
            self.expiry_task.cancel()
        if self.group:
            await self.channel_layer.group_discard(self.group, self.channel_name)
