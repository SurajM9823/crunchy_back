import base64
import io
from urllib.parse import urlencode
from django.conf import settings
from django.shortcuts import get_object_or_404
from rest_framework.response import Response
from apps.orders.pos_views import StaffAPIView
from apps.orders.pos_access import staff_branch
from .models import DiningTable
from .qr_security import generate_table_qr_token


class TableQrImageView(StaffAPIView):
    def get(self, request, table_id):
        import qrcode
        from qrcode.image.svg import SvgPathImage
        branch = staff_branch(request, 'orders')
        table = get_object_or_404(DiningTable, pk=table_id, branch=branch, is_active=True)
        token = generate_table_qr_token(table)
        origin = getattr(settings, 'FRONTEND_BASE_URL', 'https://crunchybag.com').rstrip('/')
        url = f'{origin}/table-qr?{urlencode({"token": token, "outlet_id": branch.pk})}'
        image = qrcode.make(url, image_factory=SvgPathImage, box_size=6, border=4)
        output = io.BytesIO()
        image.save(output)
        return Response({'url': url, 'table_number': table.table_number,
                         'image': 'data:image/svg+xml;base64,' + base64.b64encode(output.getvalue()).decode()})
