from django.contrib.auth.views import LogoutView
from django.urls import path

from portal import views

app_name = "portal"
urlpatterns = [
    path("", views.home, name="home"),
    path("history/", views.history, name="history"),
    path("methodology/", views.methodology, name="methodology"),
    path("check-offer/", views.check_offer, name="check-offer"),
    path("offline/", views.offline, name="offline"),
    path("service-worker.js", views.service_worker, name="service-worker"),
    path("language/", views.set_language, name="set-language"),
    path("login/", views.StaffLoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(next_page="/"), name="logout"),
    path("manage/", views.manage_dashboard, name="manage-dashboard"),
]
