from django.urls import re_path
from .pos_consumers import PosConsumer
from apps.customer_web.consumers import CustomerOrdersConsumer, AudienceRevisionConsumer
from .consumers import KitchenConsumer, LiveDisplayConsumer, OrderTrackingConsumer

websocket_urlpatterns = [
    re_path(r'^ws/outlets/(?P<outlet_id>\d+)/analytics/$', AudienceRevisionConsumer.as_asgi()),
    re_path(r'^ws/customer/orders/$', CustomerOrdersConsumer.as_asgi()),
    re_path(r'^ws/pos/(?P<outlet_id>\d+)/$', PosConsumer.as_asgi()),
    re_path(r'^ws/outlets/(?P<outlet_id>\w+)/kitchen/$', KitchenConsumer.as_asgi()),
    re_path(r'^ws/outlets/(?P<outlet_id>\w+)/display/$', LiveDisplayConsumer.as_asgi()),
    re_path(r'^ws/display/(?P<outlet_id>\w+)/$', LiveDisplayConsumer.as_asgi()),
    re_path(r'^ws/orders/(?P<order_id>\w+)/tracking/$', OrderTrackingConsumer.as_asgi()),
]

