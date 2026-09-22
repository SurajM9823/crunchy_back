"""
ASGI config for crunchy_backend project.
Handles both HTTP and WebSocket connections using Django Channels and Daphne.
"""

import os
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'crunchy_backend.settings')

# Initialize Django ASGI application early to ensure the AppRegistry is populated
django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
import apps.user_accounts.routing
import apps.restaurants.routing
import apps.catalog.routing
import apps.orders.routing

combined_websocket_urlpatterns = (
    apps.user_accounts.routing.websocket_urlpatterns
    + apps.restaurants.routing.websocket_urlpatterns
    + apps.catalog.routing.websocket_urlpatterns
    + apps.orders.routing.websocket_urlpatterns
)

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": AuthMiddlewareStack(
        URLRouter(combined_websocket_urlpatterns)
    ),
})

