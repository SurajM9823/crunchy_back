import json
from channels.generic.websocket import AsyncWebsocketConsumer


class KitchenConsumer(AsyncWebsocketConsumer):
    """
    KDS (Kitchen Display System) WebSocket Consumer (Rule 2 & Rule 3: Zero Page Reload).
    Streams active preparation tickets.
    Target URL: ws/outlets/<outlet_id>/kitchen/
    """
    async def connect(self):
        self.outlet_id = self.scope['url_route']['kwargs'].get('outlet_id')
        self.group_name = f"outlet_{self.outlet_id}_kitchen"

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

        await self.send(text_data=json.dumps({
            'event': 'CONNECTED',
            'channel': 'kitchen_kds',
            'outlet_id': self.outlet_id,
            'message': 'Connected to live KDS kitchen prep stream',
        }))

    async def disconnect(self, close_code):
        if hasattr(self, 'group_name'):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def kitchen_ticket_update(self, event):
        await self.send(text_data=json.dumps(event))

    async def order_event(self, event):
        await self.send(text_data=json.dumps(event))


class LiveDisplayConsumer(AsyncWebsocketConsumer):
    """
    TV Pickup Display Board WebSocket Consumer (Rule 3: Zero Page Reload).
    Streams preparing and ready tickets for customer waiting areas.
    Target URL: ws/outlets/<outlet_id>/display/
    """
    async def connect(self):
        self.outlet_id = self.scope['url_route']['kwargs'].get('outlet_id')
        self.group_name = f"outlet_{self.outlet_id}_display"

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

        await self.send(text_data=json.dumps({
            'event': 'CONNECTED',
            'channel': 'tv_display',
            'outlet_id': self.outlet_id,
            'message': 'Connected to live TV pickup display stream',
        }))

    async def disconnect(self, close_code):
        if hasattr(self, 'group_name'):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def display_update(self, event):
        await self.send(text_data=json.dumps(event))

    async def order_event(self, event):
        await self.send(text_data=json.dumps(event))


class OrderTrackingConsumer(AsyncWebsocketConsumer):
    """
    Customer Smartphone Live Order Progress Tracker.
    Target URL: ws/orders/<order_id>/tracking/
    """
    async def connect(self):
        self.order_id = self.scope['url_route']['kwargs'].get('order_id')
        self.group_name = f"order_{self.order_id}"

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

        await self.send(text_data=json.dumps({
            'event': 'CONNECTED',
            'channel': 'order_tracking',
            'order_id': self.order_id,
            'message': 'Connected to live order tracking stream',
        }))

    async def disconnect(self, close_code):
        if hasattr(self, 'group_name'):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def order_event(self, event):
        await self.send(text_data=json.dumps(event))

