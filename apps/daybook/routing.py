from django.urls import re_path
from .consumers import DaybookConsumer

websocket_urlpatterns = [re_path(r'^ws/daybook/(?P<outlet_id>\d+)/$', DaybookConsumer.as_asgi())]
