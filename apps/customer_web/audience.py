"""Outlet-scoped traffic aggregates and customer directory (no credentials)."""
import re
from datetime import timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView
from apps.restaurants.models import Branch
from apps.orders.models import Order
from .models import WebsiteVisit, CustomerContact
from .audience_services import record_visit, contact_phone


def audience_branch(request):
    branch_id = serializers.IntegerField(min_value=1).run_validation(request.query_params.get('outlet_id'))
    branch = Branch.objects.filter(pk=branch_id, is_active=True).first()
    user = request.user
    if not branch or not user.is_active:
        raise PermissionDenied()
    employee = getattr(user, 'employee_profile', None)
    allowed = user.is_superuser or user.role == 'RESTAURANT_OWNER' and user.restaurant_id == branch.restaurant_id
    allowed |= user.role == 'BRANCH_MANAGER' and user.branch_id == branch.pk
    allowed |= bool(employee and employee.is_active and employee.assigned_outlet_id == branch.pk and 'analytics' in (employee.assigned_pages or []))
    if not allowed:
        raise PermissionDenied('Analytics access is limited to your assigned outlet.')
    return branch


class VisitInput(serializers.Serializer):
    event_id = serializers.UUIDField()
    visitor_id = serializers.UUIDField()
    session_id = serializers.UUIDField()
    outlet_id = serializers.IntegerField(min_value=1)
    path = serializers.ChoiceField(choices=['/', '/menu', '/orders', '/profile', '/checkout', '/table-qr', '/kiosk', '/track'])
    channel = serializers.ChoiceField(choices=['WEBSITE','TABLE_QR','KIOSK'])
    device = serializers.ChoiceField(choices=['MOBILE','DESKTOP'])

    def validate_outlet_id(self, value):
        if not Branch.objects.filter(pk=value, is_active=True, restaurant__is_active=True).exists():
            raise serializers.ValidationError('Outlet unavailable.')
        return value


class VisitThrottle(AnonRateThrottle):
    rate = '120/min'


class WebsiteVisitView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [VisitThrottle]

    def post(self, request):
        serializer = VisitInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        record_visit(serializer.validated_data)
        return Response(status=204)


class WebsiteAnalyticsView(APIView):
    permission_classes = [IsAuthenticated]

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response['Cache-Control'] = 'private, no-store'
        return response

    def get(self, request):
        branch = audience_branch(request)
        days = serializers.ChoiceField(choices=[7,30,90]).run_validation(request.query_params.get('days', 30))
        days = int(days)
        today = timezone.localdate(timezone=ZoneInfo('Asia/Kathmandu'))
        start = today-timedelta(days=days-1)
        visits = WebsiteVisit.objects.filter(branch=branch, created_at__date__gte=start)
        totals = visits.aggregate(page_views=Count('pk'), visitors=Count('visitor_hash', distinct=True), sessions=Count('session_hash', distinct=True))
        daily = {str(row['date']): row for row in visits.annotate(date=TruncDate('created_at', tzinfo=ZoneInfo('Asia/Kathmandu'))).values('date').annotate(page_views=Count('pk'), visitors=Count('visitor_hash', distinct=True))}
        series = [daily.get(str(start+timedelta(days=i)), {'date':str(start+timedelta(days=i)),'page_views':0,'visitors':0}) for i in range(days)]
        return Response({**totals, 'days':days, 'daily':series,
            'channels': list(visits.values('channel').annotate(page_views=Count('pk'))),
            'pages':list(visits.values('path').annotate(page_views=Count('pk')).order_by('-page_views')[:8])})


class CustomerDirectoryView(WebsiteAnalyticsView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        branch = audience_branch(request)
        page = serializers.IntegerField(min_value=1, max_value=100000).run_validation(request.query_params.get('page',1))
        query = request.query_params.get('search','').strip()[:100]
        rows = CustomerContact.objects.filter(branch=branch).select_related('user')
        if query:
            match = Q(name__icontains=query) | Q(phone__icontains=query) | Q(user__email__icontains=query)
            phone = contact_phone(query)
            if phone and re.fullmatch(r'[+\d\s().-]+', query):
                match |= Q(phone__icontains=phone)
            rows = rows.filter(match)
        count = rows.count()
        result = list(rows.order_by('-last_seen','pk')[(page-1)*25:page*25])
        from .account_selectors import financial_orders
        stats = {row['customer_contact_id']: row for row in financial_orders(branch).filter(
            customer_contact_id__in=[row.pk for row in result]
        ).values('customer_contact_id').annotate(
            orders=Count('pk'),
            due=Sum('account_due', default=Decimal('0')),
            credit=Sum('account_credit', default=Decimal('0')),
            order_total=Sum('total_payable', filter=~Q(status='CANCELLED'), default=Decimal('0')),
        )}
        return Response({'count':count,'page':page,'page_size':25,'results':[
            {'id':row.pk,'name':row.name or 'Guest','phone':row.phone,'email':row.user.email if row.user else '',
             'registered':bool(row.user_id),'sources':row.sources,'last_seen':row.last_seen,'last_login':row.last_login,
             'due':stats.get(row.pk, {}).get('due', Decimal('0')),
             'credit':stats.get(row.pk, {}).get('credit', Decimal('0')),
             'orders':stats.get(row.pk, {}).get('orders', 0),
             'order_total':stats.get(row.pk, {}).get('order_total', Decimal('0'))} for row in result]})
