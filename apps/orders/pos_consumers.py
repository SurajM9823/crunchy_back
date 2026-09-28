import asyncio
from urllib.parse import parse_qs
from django.core import signing
from django.core.cache import cache
from django.utils import timezone
from django.contrib.auth import get_user_model
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from channels.db import database_sync_to_async
from apps.restaurants.models import Branch
from .pos_access import can_access
from .models import OrderOutboxEvent


class PosConsumer(AsyncJsonWebsocketConsumer):
    @database_sync_to_async
    def heartbeat(self):
        cache.set(f'pos:heartbeat:{self.branch_id}:{self.user_id}:{self.channel_name}', timezone.now().isoformat(),timeout=90)
        latest=OrderOutboxEvent.objects.filter(branch_id=self.branch_id).order_by('-created_at','-pk').values_list('pk',flat=True).first()
        from apps.catalog.models import MenuRevision
        revision=MenuRevision.objects.filter(branch_id=self.branch_id).values_list('revision',flat=True).first() or 0
        return {'event_type':'HEARTBEAT','revision':str(latest or ''),'menu_revision':revision}

    async def receive_json(self,content,**kwargs):
        if content.get('type')=='ping':
            if not await self.authorized():
                await self.close(code=4403)
                return
            await self.send_json(await self.heartbeat())

    @database_sync_to_async
    def authorized(self):
        user=get_user_model().objects.filter(pk=self.user_id,is_active=True).first()
        branch=Branch.objects.filter(pk=self.branch_id,is_active=True,restaurant__is_active=True).first()
        return bool(branch and can_access(user,branch,'read'))

    async def connect(self):
        self.expiry_task=None
        self.group=None
        try:
            token=parse_qs(self.scope['query_string'].decode()).get('ticket',[''])[0]
            data=signing.loads(token,salt='staff-pos-websocket',max_age=60)
            self.branch_id=int(self.scope['url_route']['kwargs']['outlet_id'])
            self.user_id=data['user_id']
            if data['branch_id']!=self.branch_id or not await self.authorized(): raise ValueError()
        except (signing.BadSignature,ValueError,KeyError):
            await self.close(code=4403)
            return
        self.group=f'pos_{self.branch_id}'
        await self.channel_layer.group_add(self.group,self.channel_name)
        await self.channel_layer.group_add(f'outlet_{self.branch_id}_menu',self.channel_name)
        await self.accept()
        self.expiry_task=asyncio.create_task(self.expire())
        await self.send_json({'event_type':'CONNECTED','outlet_id':self.branch_id})

    async def expire(self):
        await asyncio.sleep(300)
        await self.close(code=4001)

    async def disconnect(self,code):
        if self.expiry_task: self.expiry_task.cancel()
        if self.group: await self.channel_layer.group_discard(self.group,self.channel_name)
        if self.group: await self.channel_layer.group_discard(f'outlet_{self.branch_id}_menu',self.channel_name)

    async def menu_updated(self,event):
        await self.pos_event(event)

    async def pos_event(self,event):
        if not await self.authorized():
            await self.close(code=4403)
            return
        await self.send_json({k:v for k,v in event.items() if k!='type'})
