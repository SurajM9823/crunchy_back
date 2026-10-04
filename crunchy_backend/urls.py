"""
URL configuration for crunchy_backend project.
"""

from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from apps.restaurants.views import OrganizationAPIView

urlpatterns = [
    path('api/v1/loyalty/', include('apps.loyalty.urls')),
    path('api/v1/customer/', include('apps.customer_web.urls')),
    path('django-admin/', admin.site.urls),
    # Direct access to superuser authentication portal and account management
    path('', include('apps.user_accounts.urls')),
    # Organization Profile, Fiscal Tax & Brand Logo Upload
    path('api/v1/organization/', OrganizationAPIView.as_view(), name='organization-settings'),
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

urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

