import json
from channels.generic.websocket import AsyncWebsocketConsumer


class CatalogMenuConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for real-time catalog & menu updates (Rule 3: Zero Page Reload).
    Target URL: ws/outlets/<outlet_id>/menu/
    Groups:
      - outlet_<outlet_id>_menu
    """

    async def connect(self):
        self.outlet_id = self.scope['url_route']['kwargs'].get('outlet_id')
        self.group_name = f"outlet_{self.outlet_id}_menu"

        await self.channel_layer.group_add(
            self.group_name,
            self.channel_name,
        )
        await self.accept()

        # Send initial confirmation
        await self.send(text_data=json.dumps({
            'event': 'CONNECTED',
            'channel': 'menu',
            'outlet_id': self.outlet_id,
            'message': 'Connected to live menu stream (Zero-Page-Reload active)',
        }))

    async def disconnect(self, close_code):
        if hasattr(self, 'group_name'):
            await self.channel_layer.group_discard(
                self.group_name,
                self.channel_name,
            )

    async def receive(self, text_data=None, bytes_data=None):
        """
        Clients can ping for menu refresh or heartbeat.
        """
        try:
            data = json.loads(text_data) if text_data else {}
            if data.get('action') == 'ping':
                await self.send(text_data=json.dumps({'event': 'PONG'}))
        except Exception:
            pass

    async def product_availability_changed(self, event):
        """
        Triggered when an Outlet Admin marks an item sold out or available.
        Pushes payload immediately to client for instant UI badge update without page reload.
        """
        await self.send(text_data=json.dumps({
            'event': 'PRODUCT_AVAILABILITY_CHANGED',
            'product_id': event['product_id'],
            'product_name': event.get('product_name', ''),
            'is_available': event['is_available'],
            'branch_id': event['branch_id'],
            'timestamp': event.get('timestamp', ''),
        }))

    async def menu_updated(self, event):
        """
        Triggered when entire menu or pricing has been altered.
        """
        await self.send(text_data=json.dumps({
            'event': 'MENU_UPDATED',
            'branch_id': event['branch_id'],
            'timestamp': event.get('timestamp', ''),
        }))

