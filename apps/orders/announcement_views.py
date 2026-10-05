from django.core.files.storage import default_storage
from django.http import FileResponse
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from .announcement_services import pickup_text, pickup_token_text, request_audio
from .selectors import get_announcement_order


class AnnouncementQuery(serializers.Serializer):
    token = serializers.CharField(max_length=64)
    round = serializers.IntegerField(min_value=1, max_value=10000, default=1)
    part = serializers.ChoiceField(choices=['full', 'token'], default='full')


class AnnouncementThrottle(AnonRateThrottle):
    rate = '300/min'


class PickupAnnouncementView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [AnnouncementThrottle]

    def get(self, request, outlet_id):
        query = AnnouncementQuery(data=request.query_params)
        query.is_valid(raise_exception=True)
        data = query.validated_data
        order = get_announcement_order(outlet_id, data['token'], data['round'])
        text = (pickup_token_text(order.order_number, data['round']) if data['part'] == 'token'
                else pickup_text(order.order_number, data['round'], order.table.table_number if order.table_id else None))
        path, state = request_audio(text)
        if path:
            response = FileResponse(default_storage.open(path, 'rb'), content_type='audio/mpeg')
            response['Cache-Control'] = 'private, max-age=3600'
            return response
        return Response({'state': state}, status=202 if state == 'pending' else 503,
                        headers={'Retry-After': '2', 'Cache-Control': 'no-store'})
