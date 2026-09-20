from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(r'^ws/alerts/$', consumers.RestaurantAlertConsumer.as_asgi()),
    re_path(r'^ws/operations/$', consumers.RestaurantAlertConsumer.as_asgi()),
]

