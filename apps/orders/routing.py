from django.urls import re_path
from .consumers import KitchenConsumer, LiveDisplayConsumer, OrderTrackingConsumer

websocket_urlpatterns = [
    re_path(r'^ws/outlets/(?P<outlet_id>\w+)/kitchen/$', KitchenConsumer.as_asgi()),
    re_path(r'^ws/outlets/(?P<outlet_id>\w+)/display/$', LiveDisplayConsumer.as_asgi()),
    re_path(r'^ws/display/(?P<outlet_id>\w+)/$', LiveDisplayConsumer.as_asgi()),
    re_path(r'^ws/orders/(?P<order_id>\w+)/tracking/$', OrderTrackingConsumer.as_asgi()),
]

