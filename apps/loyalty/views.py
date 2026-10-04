from rest_framework.response import Response
from apps.orders.pos_access import staff_branch, can_access
from apps.orders.pos_views import StaffAPIView
from .serializers import ProgramInput
from .selectors import dashboard
from .services import save_program


class LoyaltyView(StaffAPIView):
    def get(self, request):
        branch = staff_branch(request, 'loyalty')
        return Response(dashboard(branch, request.query_params.get('search', ''),
                                  can_access(request.user, branch, 'loyalty_manage')))

    def put(self, request):
        branch = staff_branch(request, 'loyalty_manage')
        serializer = ProgramInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        program = save_program(branch, serializer.validated_data)
        return Response({'enabled': program.enabled, 'tiers': program.tiers, 'version': program.version})
