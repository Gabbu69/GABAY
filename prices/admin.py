from django.contrib import admin

from .models import (
    AuditEvent,
    ImportBatch,
    ImportRow,
    PriceObservation,
    PriceSeries,
    Source,
    VerificationEvent,
)


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    list_display = ("name", "source_type", "scope", "location_name", "is_active")
    list_filter = ("source_type", "scope", "is_active")
    search_fields = ("name", "location_name", "citation")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(PriceSeries)
class PriceSeriesAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "scope",
        "location_name",
        "classification",
        "condition",
        "is_public",
        "is_primary_public",
    )
    list_filter = (
        "scope",
        "location_level",
        "classification",
        "condition",
        "is_public",
        "is_primary_public",
    )
    search_fields = ("name", "location_name", "location_code")
    prepopulated_fields = {"slug": ("name",)}


class EvidenceAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PriceObservation)
class PriceObservationAdmin(EvidenceAdmin):
    list_display = (
        "observed_on",
        "series",
        "normalized_price",
        "source",
        "workflow_status",
        "revision",
        "is_current",
    )
    list_filter = (
        "workflow_status",
        "is_current",
        "series__scope",
        "is_outlier",
        "is_conflict",
    )
    search_fields = ("public_id", "source_reference", "notes")
    date_hierarchy = "observed_on"


@admin.register(VerificationEvent)
class VerificationEventAdmin(EvidenceAdmin):
    list_display = ("created_at", "observation", "action", "actor")
    list_filter = ("action",)
    search_fields = ("observation__public_id", "reason")


@admin.register(AuditEvent)
class AuditEventAdmin(EvidenceAdmin):
    list_display = ("created_at", "action", "entity_type", "entity_id", "actor")
    list_filter = ("action", "entity_type")
    search_fields = ("entity_id", "action")


@admin.register(ImportBatch)
class ImportBatchAdmin(EvidenceAdmin):
    list_display = ("created_at", "kind", "series", "source", "status", "total_rows")
    list_filter = ("kind", "status", "series__scope")
    search_fields = ("public_id", "original_filename", "checksum_sha256")


@admin.register(ImportRow)
class ImportRowAdmin(EvidenceAdmin):
    list_display = ("batch", "row_number", "observed_on", "normalized_price", "status")
    list_filter = ("status",)
    search_fields = ("batch__public_id", "source_reference")
