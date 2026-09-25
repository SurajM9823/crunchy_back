from django.urls import path
from django.views.generic import RedirectView
from rest_framework_simplejwt.views import TokenRefreshView
from .views import (
    SuperuserLoginView,
    SuperuserLogoutView,
    LoginAPIView,
    OutletLoginAPIView,
    UserProfileAPIView,
    StaffListAPIView,
    SystemHealthAPIView,
    EmployeeListCreateAPIView,
    EmployeeDetailAPIView,
    StaffPinLoginAPIView,
)

urlpatterns = [
    # Default landing: redirect to superuser portal
    path('', RedirectView.as_view(url='/superuser/login/', permanent=False), name='index'),

    # Superuser Web Portal
    path('superuser/login/', SuperuserLoginView.as_view(), name='superuser-login'),
    path('superuser/logout/', SuperuserLogoutView.as_view(), name='superuser-logout'),

    # REST API Endpoints
    path('api/v1/auth/login/', LoginAPIView.as_view(), name='api-login'),
    path('api/v1/auth/outlet-login/', OutletLoginAPIView.as_view(), name='api-outlet-login'),
    path('api/v1/auth/staff-pin-login/', StaffPinLoginAPIView.as_view(), name='api-staff-pin-login'),
    path('api/v1/auth/token/refresh/', TokenRefreshView.as_view(), name='api-token-refresh'),
    path('api/v1/auth/me/', UserProfileAPIView.as_view(), name='api-profile'),
    path('api/v1/auth/staff/', StaffListAPIView.as_view(), name='api-staff-list'),
    path('api/v1/health/', SystemHealthAPIView.as_view(), name='api-health'),

    # Staff & Access Management (Employees & RBAC)
    path('api/v1/employees/', EmployeeListCreateAPIView.as_view(), name='employee-list-create'),
    path('api/v1/employees/<str:pk>/', EmployeeDetailAPIView.as_view(), name='employee-detail'),
]

