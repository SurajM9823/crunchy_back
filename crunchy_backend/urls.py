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
]

