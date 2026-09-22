from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(r'^ws/outlets/(?P<outlet_id>\w+)/operations/$', consumers.OutletOperationsConsumer.as_asgi()),
]

