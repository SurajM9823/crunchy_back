from django.core import signing
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.throttling import SimpleRateThrottle
from apps.orders.pos_access import staff_branch
from . import access, selectors, services
from .serializers import SendSerializer, ReadSerializer


class ChatThrottle(SimpleRateThrottle):
    rate = '300/min'
    def get_cache_key(self, request, view):
        return self.cache_format % {'scope': 'chat', 'ident': str(request.user.pk) if request.user.is_authenticated else self.get_ident(request)}


class StartThrottle(ChatThrottle):
    rate = '20/min'
    def get_cache_key(self, request, view):
        return 'chat-start:' + super().get_cache_key(request, view)


class SendThrottle(ChatThrottle):
    rate = '60/min'
    def get_cache_key(self, request, view):
        if request.method != 'POST': return None
        return 'chat-send:' + super().get_cache_key(request, view)


class ChatView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ChatThrottle]
    staff = False
    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if self.staff and not request.user.is_authenticated:
            from rest_framework.exceptions import NotAuthenticated
            raise NotAuthenticated()
    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response['Cache-Control'] = 'private, no-store'
        return response


class StartView(ChatView):
    throttle_classes = [ChatThrottle, StartThrottle]
    def post(self, request):
        thread = services.start_conversation(request)
        return Response(selectors.history(thread, {}, request.user))


class InboxView(ChatView):
    permission_classes = [IsAuthenticated]
    def get(self, request):
        return Response(selectors.inbox(staff_branch(request, 'orders'), request.user, request.query_params))


class MessagesView(ChatView):
    throttle_classes = [ChatThrottle, SendThrottle]
    def get(self, request, conversation_id):
        thread = access.conversation_for(request, conversation_id, self.staff)
        return Response(selectors.history(thread, request.query_params, request.user, self.staff))
    def post(self, request, conversation_id):
        thread = access.conversation_for(request, conversation_id, self.staff)
        serializer = SendSerializer(data=request.data); serializer.is_valid(raise_exception=True)
        return Response(selectors.message_data(services.send_message(thread, request.user, serializer.validated_data, self.staff)), status=201)


class ReadView(ChatView):
    def post(self, request, conversation_id):
        thread = access.conversation_for(request, conversation_id, self.staff)
        serializer = ReadSerializer(data=request.data); serializer.is_valid(raise_exception=True)
        services.mark_read(thread, request.user, serializer.validated_data['last_message_id'], self.staff)
        return Response({'ok': True})


class TicketView(ChatView):
    def post(self, request):
        if self.staff:
            branch = staff_branch(request, 'orders')
            data = {'staff': True, 'user_id': request.user.pk, 'branch_id': branch.pk}
        else:
            from rest_framework import serializers
            thread_id = serializers.UUIDField().run_validation(request.data.get('conversation_id'))
            thread = access.conversation_for(request, thread_id)
            data = {'staff': False, 'user_id': request.user.pk if request.user.is_authenticated else None,
                'branch_id': thread.branch_id, 'conversation_id': str(thread.pk), 'guest_hash': thread.guest_hash if thread.customer_id is None else ''}
        return Response({'ticket': signing.dumps(data, salt='chat-socket'), 'path': '/ws/chat/'})
