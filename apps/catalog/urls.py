from django.urls import path
from .views import (
    CategoryListCreateAPIView,
    CategoryDetailAPIView,
    ProductListCreateAPIView,
    ProductDetailAPIView,
    OutletMenuAPIView,
    OutletProductToggleStockAPIView,
    PricingCalculationAPIView,
)

urlpatterns = [
    # Categories
    path('categories/', CategoryListCreateAPIView.as_view(), name='category-list-create'),
    path('categories/<str:category_id>/', CategoryDetailAPIView.as_view(), name='category-detail'),

    # Products
    path('products/', ProductListCreateAPIView.as_view(), name='product-list-create'),
    path('products/<str:product_id>/', ProductDetailAPIView.as_view(), name='product-detail'),

    # High-Scale Cached Menu
    path('menu/', OutletMenuAPIView.as_view(), name='outlet-menu'),

    # Outlet Admin Stock & Availability Toggles (Zero Page Reload Trigger)
    path('outlets/me/products/<str:product_id>/toggle-stock/', OutletProductToggleStockAPIView.as_view(), name='outlet-toggle-stock'),

    # Statutory Taxes, Cash Round-Down & Pricing Engine
    path('calculate-pricing/', PricingCalculationAPIView.as_view(), name='calculate-pricing'),
]

