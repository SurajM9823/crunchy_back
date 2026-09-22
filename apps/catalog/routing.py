from django.urls import re_path
from .consumers import CatalogMenuConsumer

websocket_urlpatterns = [
    re_path(r'^ws/outlets/(?P<outlet_id>\w+)/menu/$', CatalogMenuConsumer.as_asgi()),
]

