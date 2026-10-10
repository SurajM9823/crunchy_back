import asyncio
import time
from uuid import UUID
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
        self.group = None; self.expiry = None; self.typing_thread = None; self.last_typing = 0
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
        if self.typing_thread: await self.publish_typing(self.typing_thread, False)
        if self.expiry: self.expiry.cancel()
        if self.group: await self.channel_layer.group_discard(self.group, self.channel_name)

    @database_sync_to_async
    def typing_allowed(self, thread_id):
        return Conversation.objects.filter(pk=thread_id, branch_id=self.identity['branch_id']).exists()

    async def publish_typing(self, thread_id, active):
        group = f"chat_thread_{thread_id}" if self.identity['staff'] else f"chat_staff_{self.identity['branch_id']}"
        await self.channel_layer.group_send(group, {'type': 'chat.event', 'event_type': 'CHAT_TYPING',
            'conversation_id': thread_id, 'is_staff': self.identity['staff'], 'is_typing': active})

    async def receive_json(self, content, **kwargs):
        if not isinstance(content, dict): return
        if content.get('type') not in ('ping', 'typing'): return
        if not await self.authorized(): await self.close(code=4403); return
        if content['type'] == 'ping':
            await self.send_json({'event_type': 'HEARTBEAT', 'revision': await self.revision()})
            return
        if not isinstance(content.get('is_typing'), bool): return
        try: thread_id = str(UUID(str(content.get('conversation_id', ''))))
        except ValueError: return
        if not self.identity['staff'] and thread_id != self.identity['conversation_id']: return
        active = content['is_typing']
        if active and time.monotonic() - self.last_typing < 1: return
        if not await self.typing_allowed(thread_id): return
        if self.typing_thread and self.typing_thread != thread_id:
            await self.publish_typing(self.typing_thread, False)
        await self.publish_typing(thread_id, active)
        self.typing_thread = thread_id if active else None
        if active: self.last_typing = time.monotonic()

    async def chat_event(self, event):
        if not await self.authorized(): await self.close(code=4403); return
        await self.send_json({k: v for k, v in event.items() if k != 'type'})
