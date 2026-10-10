from django.core import signing
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.throttling import SimpleRateThrottle
from apps.orders.pos_access import staff_branch
from .client_ip import client_ip
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


class UploadThrottle(ChatThrottle):
    rate = '10/min'
    def get_cache_key(self, request, view):
        if request.method != 'POST' or not request.FILES: return None
        return 'chat-upload:' + super().get_cache_key(request, view)


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
    throttle_classes = [ChatThrottle, SendThrottle, UploadThrottle]
    def get(self, request, conversation_id):
        thread = access.conversation_for(request, conversation_id, self.staff)
        return Response(selectors.history(thread, request.query_params, request.user, self.staff))
    def post(self, request, conversation_id):
        thread = access.conversation_for(request, conversation_id, self.staff)
        serializer = SendSerializer(data=request.data); serializer.is_valid(raise_exception=True)
        return Response(selectors.message_data(services.send_message(thread, request.user, serializer.validated_data, self.staff, address=client_ip(request))), status=201)


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


class AttachmentAccessView(ChatView):
    def get(self, request, conversation_id, message_id):
        from django.shortcuts import get_object_or_404
        from .models import Message
        thread = access.conversation_for(request, conversation_id, self.staff)
        message = get_object_or_404(Message, pk=message_id, conversation=thread)
        if not message.file:
            from rest_framework.exceptions import NotFound
            raise NotFound()
        from urllib.parse import urlencode
        ticket = signing.dumps({'message_id': message.pk, 'sha256': message.file_sha256}, salt='chat-file')
        return Response({'url': '/api/v1/chat/files/?' + urlencode({'ticket': ticket}), 'expires_in': 600})


class AttachmentContentView(ChatView):
    authentication_classes = []
    def get(self, request):
        from django.http import FileResponse, StreamingHttpResponse, HttpResponse
        from django.shortcuts import get_object_or_404
        from django.utils.http import content_disposition_header
        from rest_framework.exceptions import PermissionDenied, NotFound
        from .models import Message
        import re
        try:
            payload = signing.loads(request.query_params.get('ticket', ''), salt='chat-file', max_age=600)
        except signing.BadSignature:
            raise PermissionDenied('This file link expired. Open the attachment again.')
        message = get_object_or_404(Message, pk=payload['message_id'], file_sha256=payload['sha256'],
            conversation__branch__is_active=True, conversation__branch__restaurant__is_active=True)
        if not message.file: raise NotFound()
        try: handle = message.file.open('rb')
        except (FileNotFoundError, OSError): raise NotFound('File is unavailable.')
        size = message.file_size
        ranged = request.headers.get('Range')
        if ranged:
            match = re.fullmatch(r'bytes=(\d{0,20})-(\d{0,20})', ranged)
            if not match or not any(match.groups()):
                handle.close(); response = HttpResponse(status=416); response['Content-Range'] = f'bytes */{size}'; return response
            first, last = match.groups()
            start = int(first) if first else max(0, size - int(last))
            end = min(size - 1, int(last)) if first and last else size - 1
            if start > end or start >= size or (not first and int(last) == 0):
                handle.close(); response = HttpResponse(status=416); response['Content-Range'] = f'bytes */{size}'; return response
            handle.seek(start)
            def chunks():
                remaining = end - start + 1
                try:
                    while remaining:
                        part = handle.read(min(65536, remaining))
                        if not part: break
                        remaining -= len(part)
                        yield part
                finally: handle.close()
            response = StreamingHttpResponse(chunks(), status=206, content_type=message.file_mime)
            response['Content-Length'] = end - start + 1
            response['Content-Range'] = f'bytes {start}-{end}/{size}'
        else:
            response = FileResponse(handle, content_type=message.file_mime)
        inline = message.file_mime.startswith(('image/', 'audio/', 'video/'))
        response['Content-Disposition'] = content_disposition_header(not inline, message.file_name)
        response['Accept-Ranges'] = 'bytes'
        response['X-Content-Type-Options'] = 'nosniff'
        response['Content-Security-Policy'] = "sandbox; default-src 'none'"
        response['Referrer-Policy'] = 'no-referrer'
        return response
