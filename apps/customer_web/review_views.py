from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView
from .review_services import verified_review_order, save_review


class ReviewInput(serializers.Serializer):
    token = serializers.CharField(max_length=256, required=False, allow_blank=True)
    order_number = serializers.CharField(max_length=64, required=False)
    customer_phone = serializers.CharField(max_length=32, required=False)
    rating = serializers.IntegerField(min_value=1, max_value=5)
    comment = serializers.CharField(max_length=2000, required=False, allow_blank=True, default='')


class ReviewThrottle(AnonRateThrottle):
    rate = '20/hour'


class OrderReviewView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []  # Access is proven by the receipt token or checkout phone.
    throttle_classes = [ReviewThrottle]

    def post(self, request):
        serializer = ReviewInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        order = verified_review_order(data)
        response = Response(save_review(order, data['rating'], data['comment']))
        response['Cache-Control'] = 'private, no-store'
        return response
