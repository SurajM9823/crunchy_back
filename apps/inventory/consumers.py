import json
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from channels.generic.websocket import AsyncWebsocketConsumer


class SupplierRevisionConsumer(AsyncJsonWebsocketConsumer):
    """Only revision identifiers are public; account snapshots require staff auth."""
    async def connect(self):
        self.branch_id = self.scope['url_route']['kwargs']['outlet_id']
        self.group = f'suppliers_{self.branch_id}'
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()

    @database_sync_to_async
    def revision(self):
        from django.db.models import Max
        from apps.daybook.models import DaybookEvent
        return str(DaybookEvent.objects.filter(branch_id=self.branch_id, event_type='SUPPLIER_ACCOUNT_UPDATED').aggregate(last=Max('pk'))['last'] or 0)

    async def receive_json(self, content, **kwargs):
        if content.get('type') == 'ping':
            await self.send_json({'event_type': 'HEARTBEAT', 'revision': await self.revision()})

    async def supplier_event(self, event):
        await self.send_json({key: value for key, value in event.items() if key != 'type'})

    async def disconnect(self, code):
        await self.channel_layer.group_discard(self.group, self.channel_name)


class InventoryConsumer(AsyncWebsocketConsumer):
    """
    Inventory Real-time WebSocket Consumer (Rule 3 & Principle 7: Zero Page Reload).
    Streams restock events, stock deductions by sales, audit reconciliations, and low-stock alerts.
    Target URL: ws/inventory/<outlet_id>/ or ws/outlets/<outlet_id>/inventory/
    """
    async def connect(self):
        self.outlet_id = self.scope['url_route']['kwargs'].get('outlet_id')
        self.group_name = f"inventory_{self.outlet_id}"

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

        await self.send(text_data=json.dumps({
            'event': 'CONNECTED',
            'channel': 'inventory',
            'outlet_id': self.outlet_id,
            'message': 'Connected to live inventory stock and inward purchase stream',
        }))

    async def disconnect(self, close_code):
        if hasattr(self, 'group_name'):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def inventory_event(self, event):
        """
        Handler called when group_send with type 'inventory_event' is dispatched.
        """
        await self.send(text_data=json.dumps(event))
