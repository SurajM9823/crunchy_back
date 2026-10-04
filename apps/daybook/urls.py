from django.urls import path
from .views import EntriesView, ImportView, VoidView, SocketTicketView

urlpatterns = [path('', EntriesView.as_view()), path('import/', ImportView.as_view()),
    path('<int:entry_id>/void/', VoidView.as_view()), path('socket-ticket/', SocketTicketView.as_view())]
