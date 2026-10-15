from rest_framework.response import Response

from .pos_access import staff_branch
from .pos_views import StaffAPIView
from .report_selectors import sales_report
from .report_serializers import SalesReportFilters


class SalesReportView(StaffAPIView):
    def get(self, request):
        branch = staff_branch(request, 'billing')
        filters = SalesReportFilters(data=request.query_params)
        filters.is_valid(raise_exception=True)
        return Response(sales_report(branch, filters.validated_data))
