from datetime import timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone
from rest_framework import serializers


class SalesReportFilters(serializers.Serializer):
    start_date = serializers.DateField(required=False)
    end_date = serializers.DateField(required=False)

    def validate(self, attrs):
        today = timezone.localdate(timezone=ZoneInfo('Asia/Kathmandu'))
        end = attrs.get('end_date', today)
        start = attrs.get('start_date', end - timedelta(days=6))
        if start > end:
            raise serializers.ValidationError('Start date must be on or before end date.')
        if (end - start).days >= 366:
            raise serializers.ValidationError('Select at most 366 days per report.')
        if end.year >= 9999:
            raise serializers.ValidationError('End date is outside the supported range.')
        return {'start_date': start, 'end_date': end}
