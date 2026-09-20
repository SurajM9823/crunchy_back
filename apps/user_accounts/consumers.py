import json
from channels.generic.websocket import AsyncJsonWebsocketConsumer


class RestaurantAlertConsumer(AsyncJsonWebsocketConsumer):
    """
    WebSocket consumer for real-time restaurant operational alerts,
    new orders, table callouts, and manager notifications.
    Endpoint: ws://<host>/ws/alerts/
    """
    GROUP_NAME = "restaurant_operations"

    async def connect(self):
        user = self.scope.get("user")
        
        # Add connection to the restaurant operations channel group
        await self.channel_layer.group_add(
            self.GROUP_NAME,
            self.channel_name
        )

        await self.accept()

        # Send initial connection acknowledgment
        await self.send_json({
            "type": "connection_established",
            "message": "Connected to Crunchy RMS Live Real-time Stream.",
            "authenticated": user.is_authenticated if user else False,
            "username": user.username if user and user.is_authenticated else "Guest",
        })

    async def disconnect(self, close_code):
        # Leave group
        await self.channel_layer.group_discard(
            self.GROUP_NAME,
            self.channel_name
        )

    async def receive_json(self, content, **kwargs):
        """
        Handle incoming messages from clients (e.g. ping, kitchen ready ping).
        """
        action = content.get("action", "ping")

        if action == "ping":
            await self.send_json({
                "type": "pong",
                "message": "Crunchy RMS WebSocket alive",
            })
        elif action == "broadcast_alert":
            # Broadcast message to all staff connected to the group
            message = content.get("message", "Attention: Update from staff")
            await self.channel_layer.group_send(
                self.GROUP_NAME,
                {
                    "type": "operation_alert",
                    "payload": {
                        "sender": str(self.scope.get("user", "Anonymous")),
                        "message": message,
                    }
                }
            )

    async def operation_alert(self, event):
        """
        Handler for messages pushed via channel layer to the restaurant_operations group.
        """
        await self.send_json({
            "type": "operation_alert",
            "data": event.get("payload", {})
        })

