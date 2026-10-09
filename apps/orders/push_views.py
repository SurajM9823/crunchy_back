from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import MobilePushDevice
from .pos_access import staff_branch


class MobilePushDeviceView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        branch = staff_branch(request)
        token = request.data.get('token')
        if not isinstance(token, str) or not token.strip() or len(token) > 8192:
            raise ValidationError({'token': 'A valid Firebase device token is required.'})
        platform = request.data.get('platform', 'android')
        if platform != 'android':
            raise ValidationError({'platform': 'Only Android devices are supported.'})

        MobilePushDevice.objects.update_or_create(
            token=token.strip(),
            defaults={
                'user': request.user,
                'branch': branch,
                'platform': platform,
                'active': True,
            },
        )
        return Response({'registered': True})

    def delete(self, request):
        branch = staff_branch(request)
        token = request.data.get('token')
        if not isinstance(token, str) or not token.strip() or len(token) > 8192:
            raise ValidationError({'token': 'A valid Firebase device token is required.'})
        MobilePushDevice.objects.filter(
            user=request.user,
            branch=branch,
            token=token.strip(),
        ).update(active=False)
        return Response({'registered': False})
