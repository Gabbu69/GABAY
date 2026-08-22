from django.urls import path

from . import views

app_name = "forecasting"
urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("run/", views.run_experiment_view, name="run-experiment"),
    path("rules/freeze/", views.freeze_rule_set, name="freeze-rule-set"),
]
