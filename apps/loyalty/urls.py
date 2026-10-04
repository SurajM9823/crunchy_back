from django.urls import path
from .views import LoyaltyView

urlpatterns = [path('', LoyaltyView.as_view())]
