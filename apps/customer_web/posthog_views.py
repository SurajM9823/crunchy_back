from django.shortcuts import get_object_or_404
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import serializers
from apps.restaurants.models import Branch
from .audience import WebsiteAnalyticsView, audience_branch, VisitThrottle
from .posthog_services import project_config


class TrackingConfigView(WebsiteAnalyticsView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [VisitThrottle]

    def get(self, request):
        outlet = serializers.IntegerField(min_value=1).run_validation(request.query_params.get('outlet_id'))
        branch = get_object_or_404(Branch, pk=outlet, is_active=True, restaurant__is_active=True)
        config = project_config(branch.pk)
        return Response({key: value for key, value in config.items() if key != 'project_url'})


class ReportingOverviewView(WebsiteAnalyticsView):
    def get(self, request):
        from .posthog_selectors import reporting_overview
        from .journey_selectors import ReportFilters
        branch = audience_branch(request)
        filters = ReportFilters(data=request.query_params)
        filters.is_valid(raise_exception=True)
        force_refresh = request.query_params.get('refresh') not in (None, '', '0', 'false')
        return Response(reporting_overview(branch, filters.validated_data, request.user, force_refresh=force_refresh))
