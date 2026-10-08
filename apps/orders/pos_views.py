from django.core import signing
from django.shortcuts import get_object_or_404
from django.db.models import OuterRef, Subquery
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from apps.tables.models import DiningTable, TableGroup
from .pos_access import staff_branch, can_access, require_access
from .pos_selectors import list_orders, order_queryset, order_data
from .pos_services import quote, mutate, totals, order_totals
from decimal import Decimal
from .pos_serializers import (PosListSerializer, PosQuoteSerializer, PosCreateSerializer, PosAppendSerializer,
    PosSettleSerializer, PosTransitionSerializer, PosVoidSerializer, PosRefundSerializer, VersionSerializer, PosBillQuoteSerializer, PosRoundSerializer, PosCallSerializer, PosBillSerializer)
from .models import PosReceipt, Order


class StaffAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request,response,*args,**kwargs)
        response['Cache-Control']='private, no-store'
        return response


class PosOrdersView(StaffAPIView):
    def get(self,request):
        branch=staff_branch(request)
        serializer=PosListSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        return Response(list_orders(branch,serializer.validated_data))

    def post(self,request):
        branch=staff_branch(request,'orders')
        serializer=PosCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(mutate(branch,request.user,request.headers.get('Idempotency-Key'),'create',serializer.validated_data),status=201)


class PosDashboardView(StaffAPIView):
    def get(self, request):
        from .pos_serializers import PosDashboardSerializer
        branch = staff_branch(request)
        serializer = PosDashboardSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        filters = serializer.validated_data
        data = list_orders(branch, filters)
        data['filters'] = {key: filters.get(key) for key in ('start_date', 'end_date', 'all_dates')}
        data['timezone'] = 'Asia/Kathmandu'
        return Response(data)


class PosCustomersView(StaffAPIView):
    def get(self, request):
        from apps.customer_web.customer_selectors import matching_customers
        branch = staff_branch(request, 'orders')
        return Response({'results': matching_customers(branch, request.query_params.get('search', ''))})


class PosQuoteView(StaffAPIView):
    def post(self,request):
        branch=staff_branch(request,'orders')
        serializer=PosQuoteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if serializer.validated_data['discount_amount']: require_access(request.user,branch,'discount')
        result=quote(branch,serializer.validated_data)
        if serializer.validated_data.get('order_id'):
            order=get_object_or_404(order_queryset(branch),pk=serializer.validated_data['order_id'])
            result.update(order_totals(order, subtotal=order.subtotal+Decimal(result['subtotal'])))
            result['order_version']=order.version
        return Response(result)


class PosMetaView(StaffAPIView):
    def get(self,request):
        branch=staff_branch(request)
        active_orders=Order.objects.filter(branch=branch,table_id=OuterRef('pk'),status__in=['PENDING','ACCEPTED','PREPARING','READY','OUT_FOR_DELIVERY']).order_by('-created_at')
        return Response({'outlet_id':branch.pk,'outlet_name':branch.name,
            'table_groups':list(TableGroup.objects.filter(branch=branch).values('id','name')),
            'inactive_tables':list(DiningTable.objects.filter(branch=branch,is_active=False).values('id','table_number','capacity','section','is_active')),
            'tables':list(DiningTable.objects.filter(branch=branch,is_active=True).annotate(active_order_id=Subquery(active_orders.values('pk')[:1])).order_by('section','table_number').values('id','table_number','capacity','section','active_order_id')),
            'permissions':{c:can_access(request.user,branch,c) for c in ['orders','billing','kitchen','discount','refund','delete_order']},
            'fulfillment_modes':[name for name,field in [('DINE_IN','enable_dine_in'),('TAKEAWAY','enable_takeaway'),('DELIVERY','enable_delivery'),('DRIVE_THRU','enable_drive_thru')] if getattr(branch,field)],
            'payment_methods':[name for name,field in [('CASH','enable_cash'),('CARD','enable_card'),('FONEPAY','enable_fonepay'),('ESEWA','enable_esewa'),('KHALTI','enable_khalti')] if getattr(branch.restaurant,field)]+['BANK_TRANSFER','CREDIT'],
            'accepting_orders':branch.accepting_orders and branch.enable_pos})


class PosLayoutView(StaffAPIView):
    def post(self, request, kind, object_id=None):
        from apps.tables.pos_layout import GroupInput, TableInput, save_layout
        branch = staff_branch(request, 'orders')
        serializer = (GroupInput if kind == 'group' else TableInput)(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(save_layout(branch, request.user, request.headers.get('Idempotency-Key'),
                                    kind, serializer.validated_data, object_id))


class PosDetailView(StaffAPIView):
    def get(self,request,order_id):
        branch=staff_branch(request)
        return Response(order_data(get_object_or_404(order_queryset(branch),pk=order_id)))


class PosDeleteView(StaffAPIView):
    def post(self, request, order_id):
        from .pos_serializers import PosDeleteSerializer
        from .deletion import delete_order
        branch = staff_branch(request, 'delete_order')
        serializer = PosDeleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(delete_order(branch, request.user, request.headers.get('Idempotency-Key'),
                                     order_id, serializer.validated_data))


class PosBillQuoteView(StaffAPIView):
    def post(self,request,order_id):
        branch=staff_branch(request,'billing')
        serializer=PosBillQuoteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        order=get_object_or_404(order_queryset(branch),pk=order_id)
        from .pos_services import Conflict
        if order.version!=serializer.validated_data['version']: raise Conflict()
        old_manual = Decimal(order.pricing_policy.get('manual_discount_amount', str(order.discount_amount)))
        discount = serializer.validated_data.get('discount_amount', old_manual)
        if discount != old_manual: require_access(request.user,branch,'discount')
        if order.billed_at or order.paid_amount or order.credit_amount:
            if discount not in (old_manual, order.discount_amount):
                raise Conflict('This bill is already locked.')
            result = order_totals(order)
        else:
            result = order_totals(order, manual=discount, phone=serializer.validated_data.get('customer_phone'))
        result['due_amount']=str(Decimal(result['total_payable'])-order.paid_amount)
        return Response(result)


class PosCommandView(StaffAPIView):
    def post(self,request,order_id,action):
        branch=staff_branch(request)
        serializers={'append':PosAppendSerializer,'settle':PosSettleSerializer,'transition':PosTransitionSerializer,
            'void':PosVoidSerializer,'refund':PosRefundSerializer,'call':PosCallSerializer,'round':PosRoundSerializer,'bill':PosBillSerializer}
        serializer=serializers[action](data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(mutate(branch,request.user,request.headers.get('Idempotency-Key'),action,serializer.validated_data,order_id))


class PosReceiptView(StaffAPIView):
    def get(self,request,receipt_id):
        branch=staff_branch(request)
        row=get_object_or_404(PosReceipt,pk=receipt_id,order__branch=branch)
        from .receipts import receipt_document
        return Response(receipt_document(row, request))


class PosSocketTicketView(StaffAPIView):
    def post(self,request):
        branch=staff_branch(request)
        ticket=signing.dumps({'user_id':request.user.pk,'branch_id':branch.pk},salt='staff-pos-websocket')
        return Response({'ticket':ticket,'expires_in':60,'path':f'/ws/pos/{branch.pk}/'})
