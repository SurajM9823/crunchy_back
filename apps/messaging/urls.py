from django.urls import path
from .views import StartView, InboxView, MessagesView, ReadView, TicketView

urlpatterns = [
    path('start/', StartView.as_view()),
    path('socket-ticket/', TicketView.as_view()),
    path('conversations/<uuid:conversation_id>/messages/', MessagesView.as_view()),
    path('conversations/<uuid:conversation_id>/read/', ReadView.as_view()),
    path('staff/', InboxView.as_view()),
    path('staff/socket-ticket/', TicketView.as_view(staff=True)),
    path('staff/conversations/<uuid:conversation_id>/messages/', MessagesView.as_view(staff=True)),
    path('staff/conversations/<uuid:conversation_id>/read/', ReadView.as_view(staff=True)),
]
