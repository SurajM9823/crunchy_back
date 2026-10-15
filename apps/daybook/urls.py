from django.urls import path
from .views import EntriesView, ImportView, VoidView, SocketTicketView, StaffChoicesView, KhajaScheduleView, AttendanceView

urlpatterns = [path('attendance/', AttendanceView.as_view()), path('khaja-schedule/', KhajaScheduleView.as_view()), path('staff/', StaffChoicesView.as_view()), path('', EntriesView.as_view()), path('import/', ImportView.as_view()),
    path('<int:entry_id>/void/', VoidView.as_view()), path('socket-ticket/', SocketTicketView.as_view())]
