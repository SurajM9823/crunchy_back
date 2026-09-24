"""
URL configuration for crunchy_backend project.
"""

from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView

urlpatterns = [
    path('admin/', admin.site.urls),
    # Direct access to superuser authentication portal and account management
    path('', include('apps.user_accounts.urls')),
    # Restaurant Brands & Franchise Outlets API
    path('api/v1/restaurants/', include('apps.restaurants.urls')),
    # Menu & Product Catalog Engine
    path('api/v1/catalog/', include('apps.catalog.urls')),
    # Dining Tables & QR Portal
    path('api/v1/tables/', include('apps.tables.urls')),
    # Centralized Order Engine & Checkout
    path('api/v1/orders/', include('apps.orders.urls')),
    # Payments, Settlement & Statutory Invoicing
    path('api/v1/payments/', include('apps.payments.urls')),
    # Atomic Stock & Recipe Inventory Engine
    path('api/v1/inventory/', include('apps.inventory.urls')),
    # Delivery Fleet & Live Dispatch Engine
    path('api/v1/delivery/', include('apps.delivery.urls')),
]

