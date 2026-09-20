from django.urls import path
from .views import (
    RestaurantListCreateAPIView,
    RestaurantDetailAPIView,
    BranchListCreateAPIView,
    BranchDetailAPIView,
)

urlpatterns = [
    # Restaurant Brands
    path('', RestaurantListCreateAPIView.as_view(), name='restaurant-list-create'),
    path('<str:identifier>/', RestaurantDetailAPIView.as_view(), name='restaurant-detail'),

    # Franchise Branch Outlets
    path('<int:restaurant_id>/branches/', BranchListCreateAPIView.as_view(), name='branch-list-create'),
    path('branches/<str:identifier>/', BranchDetailAPIView.as_view(), name='branch-detail'),
]

