import asyncio
from urllib.parse import parse_qs
from django.core import signing
from django.contrib.auth import get_user_model
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from apps.restaurants.models import Branch
from apps.orders.pos_access import can_access
from .models import Conversation


class ChatConsumer(AsyncJsonWebsocketConsumer):
    @database_sync_to_async
    def authorized(self):
        data = self.identity
        branch = Branch.objects.filter(pk=data['branch_id'], is_active=True, restaurant__is_active=True).first()
        if not branch: return False
        user = get_user_model().objects.filter(pk=data.get('user_id'), is_active=True).first()
        if data['staff']: return can_access(user, branch, 'orders')
        qs = Conversation.objects.filter(pk=data['conversation_id'], branch=branch)
        if data.get('user_id'): return user is not None and qs.filter(customer=user).exists()
        return qs.filter(customer__isnull=True, guest_hash=data['guest_hash']).exists()

    @database_sync_to_async
    def revision(self):
        from django.db.models import Sum
        qs = Conversation.objects.filter(branch_id=self.identity['branch_id'])
        if not self.identity['staff']: qs = qs.filter(pk=self.identity['conversation_id'])
        return qs.aggregate(value=Sum('revision'))['value'] or 0

    async def connect(self):
        self.group = None; self.expiry = None
        try:
            token = parse_qs(self.scope['query_string'].decode()).get('ticket', [''])[0]
            self.identity = signing.loads(token, salt='chat-socket', max_age=60)
            if not await self.authorized(): raise ValueError()
        except (signing.BadSignature, ValueError, KeyError):
            await self.close(code=4403); return
        self.group = f"chat_staff_{self.identity['branch_id']}" if self.identity['staff'] else f"chat_thread_{self.identity['conversation_id']}"
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()
        self.expiry = asyncio.create_task(self.expire())
        await self.send_json({'event_type': 'CONNECTED'})

    async def expire(self):
        await asyncio.sleep(300)
        await self.close(code=4001)

    async def disconnect(self, code):
        if self.expiry: self.expiry.cancel()
        if self.group: await self.channel_layer.group_discard(self.group, self.channel_name)

    async def receive_json(self, content, **kwargs):
        if not isinstance(content, dict) or content.get('type') != 'ping': return
        if not await self.authorized(): await self.close(code=4403); return
        await self.send_json({'event_type': 'HEARTBEAT', 'revision': await self.revision()})

    async def chat_event(self, event):
        if not await self.authorized(): await self.close(code=4403); return
        await self.send_json({k: v for k, v in event.items() if k != 'type'})
