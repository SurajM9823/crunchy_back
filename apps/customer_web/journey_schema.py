"""The collection allowlist is deliberately smaller than arbitrary browser payloads."""
import re
from datetime import timedelta
from urllib.parse import urlsplit
from django.utils import timezone
from rest_framework import serializers

SERVER_EVENTS = {'order_success', 'order_confirmed', 'payment_success'}
EVENTS = set('''page_view landing_page_view session_start route_change back_navigation exit heartbeat
menu_view product_view product_detail_view product_image_view product_search category_view
add_to_cart remove_from_cart quantity_increase quantity_decrease cart_view cart_clear
coupon_view coupon_apply coupon_fail checkout_start name_entered phone_entered address_entered
location_selected delivery_information delivery_area_check delivery_area_success delivery_area_failed
delivery_fee_view payment_method_view payment_method_selected payment_started payment_failed payment_cancelled
payment_proof_uploaded order_submit order_failed order_now_click buy_now_click add_to_cart_click
checkout_click confirm_order_click call_click whatsapp_click menu_click delivery_info_click offer_click
scroll javascript_error api_error network_error image_error performance api_timing auth_required checkout_validation_failed'''.split())
ATTRIBUTION = 'utm_source utm_medium utm_campaign utm_content utm_term fbclid campaign_id campaign_name adset_id adset_name ad_id ad_name referrer'.split()
TEXT_FIELDS = set('product_id product_name category payment_method provider error_category endpoint method metric network area fulfillment_type attempt_id'.split())
NUMBER_FIELDS = set('quantity unit_price cart_value delivery_fee total duration_ms value scroll_percent status search_length result_count'.split())

def safe_text(value, length=120):
    value = str(value)[:length]
    # Do not retain accidental emails, phone numbers, credentials or arbitrary URLs.
    if re.search(r'@|(?:\+?\d[\s().-]*){9,}|password|bearer |token=|secret=|cvv', value, re.I):
        return '[redacted]'
    return re.sub(r'[\x00-\x1f<>]', '', value)

def safe_path(value):
    path = urlsplit(value).path[:200] or '/'
    if not path.startswith('/') or path.startswith('//'):
        return '/'
    path = re.sub(r'/[0-9a-fA-F-]{20,}(?=/|$)', '/:id', path)
    path = re.sub(r'/\d+(?=/|$)', '/:id', path)
    return safe_text(path, 200)

class IdentityInput(serializers.Serializer):
    visitor_id = serializers.UUIDField()
    session_id = serializers.UUIDField()
    posthog_session_id = serializers.UUIDField(required=False)

class EventInput(serializers.Serializer):
    event_id = serializers.UUIDField()
    name = serializers.ChoiceField(choices=sorted(EVENTS))
    timestamp = serializers.DateTimeField()
    path = serializers.CharField(max_length=500)
    metadata = serializers.DictField(default=dict)

    def validate_timestamp(self, value):
        now = timezone.now()
        if value > now + timedelta(minutes=5) or value < now - timedelta(hours=48):
            raise serializers.ValidationError('Event timestamp outside the accepted 48-hour window.')
        return value

    def validate_path(self, value):
        return safe_path(value)

    def validate_metadata(self, value):
        clean = {}
        for key in TEXT_FIELDS & value.keys():
            clean[key] = safe_path(str(value[key])) if key == 'endpoint' else re.sub(r'[^A-Za-z0-9_.-]', '', str(value[key]))[:120] if key.endswith('_id') else safe_text(value[key])
        for key in NUMBER_FIELDS & value.keys():
            clean[key] = serializers.FloatField(min_value=0, max_value=1e10).run_validation(value[key])
        if 'items' in value:
            if not isinstance(value['items'], list) or len(value['items']) > 100:
                raise serializers.ValidationError('Invalid cart snapshot.')
            clean['items'] = []
            for item in value['items']:
                if not isinstance(item, dict):
                    raise serializers.ValidationError('Invalid cart item.')
                clean['items'].append({key: safe_text(item[key]) if key in TEXT_FIELDS else serializers.FloatField(min_value=0, max_value=1e10).run_validation(item[key])
                                      for key in ('product_id', 'product_name', 'quantity', 'unit_price') if key in item})
        return clean

class BatchInput(IdentityInput):
    outlet_id = serializers.IntegerField(min_value=1)
    device = serializers.ChoiceField(choices=['MOBILE', 'DESKTOP', 'TABLET', 'UNKNOWN'], default='UNKNOWN')
    browser = serializers.ChoiceField(choices=['Chrome', 'Safari', 'Firefox', 'Edge', 'Facebook', 'Other', 'Unknown'], default='Unknown')
    os = serializers.ChoiceField(choices=['Android', 'iOS', 'Windows', 'macOS', 'Linux', 'Other', 'Unknown'], default='Unknown')
    network = serializers.ChoiceField(choices=['', 'slow-2g', '2g', '3g', '4g'], default='')
    attribution = serializers.DictField(default=dict)
    events = EventInput(many=True, allow_empty=False, max_length=30)

    def validate_attribution(self, value):
        return {key: re.sub(r'[^A-Za-z0-9_.-]', '', str(value[key]))[:200] if key.endswith('_id') or key=='fbclid' else safe_text(value[key])
                for key in ATTRIBUTION if key in value and value[key]}
