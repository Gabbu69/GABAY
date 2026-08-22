from django.contrib import admin

from .models import (
    DatasetSnapshot,
    DecisionRuleSet,
    FoldMetric,
    Forecast,
    ModelRun,
    Recommendation,
)


class AppendOnlyEvidenceAdmin:
    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(DatasetSnapshot)
class DatasetSnapshotAdmin(AppendOnlyEvidenceAdmin, admin.ModelAdmin):
    list_display = ("series", "cutoff_date", "observed_week_count", "coverage_ratio")
    search_fields = ("fingerprint", "series__name")
    readonly_fields = tuple(field.name for field in DatasetSnapshot._meta.fields)


class FoldMetricInline(admin.TabularInline):
    model = FoldMetric
    extra = 0
    can_delete = False
    readonly_fields = tuple(field.name for field in FoldMetric._meta.fields if field.name != "id")


@admin.register(ModelRun)
class ModelRunAdmin(AppendOnlyEvidenceAdmin, admin.ModelAdmin):
    list_display = ("id", "snapshot", "status", "selected_model", "is_live", "created_at")
    list_filter = ("status", "is_live", "selected_model")
    readonly_fields = tuple(field.name for field in ModelRun._meta.fields)
    inlines = (FoldMetricInline,)


@admin.register(Forecast)
class ForecastAdmin(AppendOnlyEvidenceAdmin, admin.ModelAdmin):
    list_display = ("series", "target_week", "kind", "point_forecast", "model_name")
    list_filter = ("kind", "model_name")
    readonly_fields = tuple(field.name for field in Forecast._meta.fields)


@admin.register(Recommendation)
class RecommendationAdmin(AppendOnlyEvidenceAdmin, admin.ModelAdmin):
    list_display = ("forecast", "decision", "reliability", "rule_set", "created_at")
    list_filter = ("decision", "reliability", "is_demo")
    readonly_fields = tuple(field.name for field in Recommendation._meta.fields)


@admin.register(DecisionRuleSet)
class DecisionRuleSetAdmin(admin.ModelAdmin):
    list_display = ("version", "status", "created_at", "frozen_at")
    list_filter = ("status",)
    readonly_fields = ("created_at", "frozen_at")

    def has_delete_permission(self, request, obj=None):
        return False
