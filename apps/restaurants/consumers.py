import json
from channels.generic.websocket import AsyncJsonWebsocketConsumer


class OutletOperationsConsumer(AsyncJsonWebsocketConsumer):
    """
    WebSocket consumer for live outlet operations:
    - Kitchen pause/resume alerts (zero page reload)
    - Live delivery toggles
    - Channel capability updates
    Endpoint: ws://<host>/ws/outlets/<outlet_id>/operations/
    """

    async def connect(self):
        self.outlet_id = self.scope['url_route']['kwargs'].get('outlet_id')
        self.group_name = f"outlet_{self.outlet_id}_operations"
        user = self.scope.get("user")

        # Join the outlet operations group
        await self.channel_layer.group_add(
            self.group_name,
            self.channel_name
        )

        await self.accept()

        # Send initial confirmation
        await self.send_json({
            "type": "outlet_stream_connected",
            "outlet_id": self.outlet_id,
            "authenticated": user.is_authenticated if user else False,
            "message": f"Connected to live operations stream for Outlet #{self.outlet_id}",
        })

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(
            self.group_name,
            self.channel_name
        )

    async def outlet_status_event(self, event):
        """
        Handler for OUTLET_STATUS_CHANGED events broadcast by services.
        Pushes live changes to connected customer phones, KDS, and Kiosks without page reload.
        """
        await self.send_json(event.get("payload", {}))

