from django.urls import path
from .views import (
    DeliveryZoneListCreateAPIView,
    RiderListCreateAPIView,
    DeliveryDispatchListAPIView,
    DeliveryDispatchAssignAPIView,
    DeliveryDispatchStatusAPIView,
    RiderLocationPingAPIView,
    CustomerDeliveryTrackingAPIView,
)

urlpatterns = [
    # Delivery Zones
    path('zones/', DeliveryZoneListCreateAPIView.as_view(), name='delivery-zone-list-create'),

    # Fleet Riders
    path('riders/', RiderListCreateAPIView.as_view(), name='delivery-rider-list-create'),

    # Active Dispatches Board
    path('dispatches/', DeliveryDispatchListAPIView.as_view(), name='delivery-dispatch-list'),
    path('dispatches/<int:dispatch_id>/assign/', DeliveryDispatchAssignAPIView.as_view(), name='delivery-dispatch-assign'),
    path('dispatches/<int:dispatch_id>/status/', DeliveryDispatchStatusAPIView.as_view(), name='delivery-dispatch-status'),

    # Ephemeral GPS Ping (High-frequency)
    path('rider/location/', RiderLocationPingAPIView.as_view(), name='delivery-rider-location-ping'),

    # Public Customer Door-to-Door Live Tracking
    path('dispatches/<str:identifier>/track/', CustomerDeliveryTrackingAPIView.as_view(), name='delivery-customer-track'),
]

