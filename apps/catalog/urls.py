from django.urls import path
from .views import (
    CategoryListCreateAPIView,
    CategoryDetailAPIView,
    ProductListCreateAPIView,
    ProductDetailAPIView,
    OutletMenuAPIView,
    OutletProductToggleStockAPIView,
    PricingCalculationAPIView,
    ManagementSnapshotAPIView, ScheduleListAPIView, ScheduleDetailAPIView, QuoteAPIView, MenuImageAPIView,
)

urlpatterns = [
    path('images/', MenuImageAPIView.as_view(), name='catalog-image'),
    path('management/', ManagementSnapshotAPIView.as_view(), name='catalog-management'),
    path('schedules/', ScheduleListAPIView.as_view(), name='catalog-schedules'),
    path('schedules/<int:schedule_id>/', ScheduleDetailAPIView.as_view(), name='catalog-schedule-detail'),
    path('quote/', QuoteAPIView.as_view(), name='catalog-quote'),
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
