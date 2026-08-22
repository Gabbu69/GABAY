from django.urls import path

from . import views

app_name = "prices"
urlpatterns = [
    path("", views.management_dashboard, name="dashboard"),
    path("series/public-selection/", views.series_selection, name="series-selection"),
    path("observations/", views.observation_list, name="observation-list"),
    path("observations/new/", views.observation_create, name="observation-create"),
    path("observations/<uuid:public_id>/", views.observation_detail, name="observation-detail"),
    path(
        "observations/<uuid:public_id>/approve/",
        views.observation_approve,
        name="observation-approve",
    ),
    path(
        "observations/<uuid:public_id>/reject/",
        views.observation_reject,
        name="observation-reject",
    ),
    path(
        "observations/<uuid:public_id>/revise/",
        views.observation_revise,
        name="observation-revise",
    ),
    path("imports/", views.import_list, name="import-list"),
    path("imports/new/", views.import_create, name="import-create"),
    path("imports/template.csv", views.csv_template, name="csv-template"),
    path("imports/<uuid:public_id>/", views.import_detail, name="import-detail"),
    path(
        "imports/<uuid:public_id>/errors.csv",
        views.import_errors,
        name="import-errors",
    ),
    path(
        "imports/<uuid:public_id>/rows/<int:row_number>/approve/",
        views.import_row_approve,
        name="import-row-approve",
    ),
    path(
        "imports/<uuid:public_id>/rows/<int:row_number>/reject/",
        views.import_row_reject,
        name="import-row-reject",
    ),
]
