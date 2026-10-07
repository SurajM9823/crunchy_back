from django.urls import re_path
from .consumers import InventoryConsumer, SupplierRevisionConsumer

websocket_urlpatterns = [
    re_path(r'^ws/outlets/(?P<outlet_id>\d+)/suppliers/$', SupplierRevisionConsumer.as_asgi()),
    re_path(r'^ws/inventory/(?P<outlet_id>[\w-]+)/$', InventoryConsumer.as_asgi()),
    re_path(r'^ws/outlets/(?P<outlet_id>[\w-]+)/inventory/$', InventoryConsumer.as_asgi()),
]
