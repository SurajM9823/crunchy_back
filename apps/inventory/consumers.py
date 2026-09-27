import json
from channels.generic.websocket import AsyncWebsocketConsumer


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
