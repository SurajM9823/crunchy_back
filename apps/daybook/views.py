from django.core import signing
from rest_framework.response import Response
from apps.orders.pos_views import StaffAPIView
from apps.orders.pos_access import staff_branch, can_access
from . import selectors, services, serializers


class EntriesView(StaffAPIView):
    def get(self, request):
        branch = staff_branch(request, 'daybook')
        query = serializers.LedgerQuery(data=request.query_params)
        query.is_valid(raise_exception=True)
        return Response(selectors.ledger(branch, query.validated_data, can_access(request.user, branch, 'daybook_void')))

    def post(self, request):
        branch = staff_branch(request, 'daybook')
        serializer = serializers.EntryInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(services.mutate(branch, request.user, request.headers.get('Idempotency-Key'),
            'create', serializer.validated_data), status=201)


class ImportView(StaffAPIView):
    def get(self, request):
        branch = staff_branch(request, 'daybook')
        query = serializers.ImportQuery(data=request.query_params)
        query.is_valid(raise_exception=True)
        return Response(selectors.import_preview(branch, **query.validated_data))

    def post(self, request):
        branch = staff_branch(request, 'daybook')
        serializer = serializers.ImportInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(services.mutate(branch, request.user, request.headers.get('Idempotency-Key'),
            'import', serializer.validated_data))


class VoidView(StaffAPIView):
    def post(self, request, entry_id):
        branch = staff_branch(request, 'daybook_void')
        serializer = serializers.VoidInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(services.mutate(branch, request.user, request.headers.get('Idempotency-Key'),
            'void', serializer.validated_data, entry_id))


class SocketTicketView(StaffAPIView):
    def post(self, request):
        branch = staff_branch(request, 'daybook')
        ticket = signing.dumps({'user_id': request.user.pk, 'branch_id': branch.pk}, salt='daybook-socket')
        return Response({'ticket': ticket, 'path': f'/ws/daybook/{branch.pk}/'})
