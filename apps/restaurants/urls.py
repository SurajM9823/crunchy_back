from django.urls import path
from .views import (
    RestaurantListCreateAPIView,
    RestaurantDetailAPIView,
    BranchListCreateAPIView,
    BranchDetailAPIView,
    OutletCurrentDetailAPIView,
    OutletStatusToggleAPIView,
    OutletDashboardSummaryAPIView,
)

urlpatterns = [
    # Outlet Admin Operations (Current Admin Scoped)
    path('outlets/me/', OutletCurrentDetailAPIView.as_view(), name='outlet-me'),
    path('outlets/me/toggle-orders/', OutletStatusToggleAPIView.as_view(), name='outlet-toggle-orders'),
    path('outlets/me/summary/', OutletDashboardSummaryAPIView.as_view(), name='outlet-summary'),

    # Restaurant Brands
    path('', RestaurantListCreateAPIView.as_view(), name='restaurant-list-create'),
    path('<str:identifier>/', RestaurantDetailAPIView.as_view(), name='restaurant-detail'),

    # Franchise Branch Outlets
    path('<int:restaurant_id>/branches/', BranchListCreateAPIView.as_view(), name='branch-list-create'),
    path('branches/<str:identifier>/', BranchDetailAPIView.as_view(), name='branch-detail'),
]

