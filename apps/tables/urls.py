from django.urls import path
from .views import (
    DiningTableListCreateAPIView,
    DiningTableDetailAPIView,
    DiningTableRegenerateQRAPIView,
    TableQRResolveAPIView,
)

urlpatterns = [
    # Table Management for Outlet Admin
    path('', DiningTableListCreateAPIView.as_view(), name='table-list-create'),
    path('<int:table_id>/', DiningTableDetailAPIView.as_view(), name='table-detail'),
    path('<int:table_id>/regenerate-qr/', DiningTableRegenerateQRAPIView.as_view(), name='table-regenerate-qr'),

    # Public Stateless Opaque QR Token Resolver
    path('qr/resolve/', TableQRResolveAPIView.as_view(), name='table-qr-resolve'),
]

