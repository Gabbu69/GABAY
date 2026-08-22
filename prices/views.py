from __future__ import annotations

import csv
import io

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .csv_import import REQUIRED_HEADERS, import_error_report, stage_csv_import
from .forms import (
    CSVImportForm,
    PriceEvidenceForm,
    PrimarySeriesForm,
    RejectionForm,
    RevisionForm,
    VerificationForm,
)
from .models import ImportBatch, ImportRow, PriceObservation, PriceSeries
from .permissions import (
    can_administer_research,
    can_encode,
    can_verify,
    encoder_required,
    research_admin_required,
    verifier_required,
)
from .services import (
    approve_import_row,
    approve_observation,
    create_price_observation,
    create_revision,
    reject_import_row,
    reject_observation,
    select_primary_public_series,
)


def _assert_management_access(user) -> None:
    if not (can_encode(user) or can_verify(user)):
        raise PermissionDenied


def _validation_message(exc: ValidationError) -> str:
    if hasattr(exc, "message_dict"):
        return "; ".join(
            f"{field}: {', '.join(values)}" for field, values in exc.message_dict.items()
        )
    return "; ".join(exc.messages)


@login_required
def management_dashboard(request):
    _assert_management_access(request.user)
    observations = PriceObservation.objects.select_related("series", "source", "created_by")
    batches = ImportBatch.objects.select_related("series", "source", "uploaded_by")
    return render(
        request,
        "prices/dashboard.html",
        {
            "pending_count": observations.pending().count(),
            "verified_count": observations.current().verified().count(),
            "review_batch_count": batches.filter(
                status=ImportBatch.Status.NEEDS_REVIEW
            ).count(),
            "recent_observations": observations[:8],
            "recent_batches": batches[:5],
            "can_encode": can_encode(request.user),
            "can_verify": can_verify(request.user),
            "can_administer_research": can_administer_research(request.user),
            "primary_series": PriceSeries.objects.filter(is_primary_public=True),
        },
    )


@research_admin_required
def series_selection(request):
    form = PrimarySeriesForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            series = select_primary_public_series(
                series=form.cleaned_data["series"], actor=request.user
            )
        except ValidationError as exc:
            form.add_error(None, _validation_message(exc))
        else:
            messages.success(
                request,
                f"{series.name} is now the explicit public series for "
                f"{series.get_scope_display()}.",
            )
            return redirect("prices:dashboard")
    return render(
        request,
        "prices/series_selection.html",
        {
            "form": form,
            "primary_series": PriceSeries.objects.filter(is_primary_public=True),
        },
    )


@login_required
def observation_list(request):
    _assert_management_access(request.user)
    status = request.GET.get("status", "").strip()
    observations = PriceObservation.objects.select_related("series", "source", "created_by")
    if status in PriceObservation.Status.values:
        observations = observations.filter(workflow_status=status)
    return render(
        request,
        "prices/observation_list.html",
        {
            "observations": observations[:250],
            "selected_status": status,
            "statuses": PriceObservation.Status.choices,
            "can_encode": can_encode(request.user),
            "can_verify": can_verify(request.user),
        },
    )


@encoder_required
def observation_create(request):
    form = PriceEvidenceForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            observation = create_price_observation(
                **form.cleaned_data,
                created_by=request.user,
            )
        except ValidationError as exc:
            form.add_error(None, _validation_message(exc))
        else:
            messages.success(request, "Price evidence was submitted for separate verification.")
            return redirect("prices:observation-detail", public_id=observation.public_id)
    return render(request, "prices/observation_form.html", {"form": form, "mode": "create"})


@login_required
def observation_detail(request, public_id):
    _assert_management_access(request.user)
    observation = get_object_or_404(
        PriceObservation.objects.select_related(
            "series", "source", "created_by", "supersedes", "import_batch"
        ),
        public_id=public_id,
    )
    return render(
        request,
        "prices/observation_detail.html",
        {
            "observation": observation,
            "events": observation.verification_events.select_related("actor"),
            "verification_form": VerificationForm(),
            "rejection_form": RejectionForm(),
            "can_encode": can_encode(request.user),
            "can_verify": can_verify(request.user),
            "is_own": observation.created_by_id == request.user.pk,
        },
    )


@require_POST
@verifier_required
def observation_approve(request, public_id):
    observation = get_object_or_404(PriceObservation, public_id=public_id)
    form = VerificationForm(request.POST)
    if form.is_valid():
        try:
            approve_observation(
                observation,
                verifier=request.user,
                reason=form.cleaned_data["reason"],
                allow_outlier=form.cleaned_data["accept_outlier"],
                allow_conflict=form.cleaned_data["accept_conflict"],
            )
        except ValidationError as exc:
            messages.error(request, _validation_message(exc))
        else:
            messages.success(request, "Observation approved and published.")
    else:
        messages.error(request, "The verification decision was invalid.")
    return redirect("prices:observation-detail", public_id=public_id)


@require_POST
@verifier_required
def observation_reject(request, public_id):
    observation = get_object_or_404(PriceObservation, public_id=public_id)
    form = RejectionForm(request.POST)
    if form.is_valid():
        try:
            reject_observation(
                observation, verifier=request.user, reason=form.cleaned_data["reason"]
            )
        except ValidationError as exc:
            messages.error(request, _validation_message(exc))
        else:
            messages.success(request, "Observation rejected with an audit reason.")
    else:
        messages.error(request, "A rejection reason is required.")
    return redirect("prices:observation-detail", public_id=public_id)


@encoder_required
def observation_revise(request, public_id):
    original = get_object_or_404(PriceObservation, public_id=public_id)
    form = RevisionForm(request.POST or None, original=original)
    if request.method == "POST" and form.is_valid():
        values = form.cleaned_data.copy()
        values.pop("series", None)
        values.pop("source", None)
        reason = values.pop("reason")
        try:
            revision = create_revision(
                original=original,
                created_by=request.user,
                reason=reason,
                **values,
            )
        except ValidationError as exc:
            form.add_error(None, _validation_message(exc))
        else:
            messages.success(request, "Revision submitted; the verified version remains live.")
            return redirect("prices:observation-detail", public_id=revision.public_id)
    return render(
        request,
        "prices/observation_form.html",
        {"form": form, "mode": "revision", "original": original},
    )


@login_required
def import_list(request):
    _assert_management_access(request.user)
    batches = ImportBatch.objects.select_related("series", "source", "uploaded_by").annotate(
        row_count=Count("rows")
    )
    return render(
        request,
        "prices/import_list.html",
        {"batches": batches[:100], "can_encode": can_encode(request.user)},
    )


@encoder_required
def import_create(request):
    form = CSVImportForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        upload = form.cleaned_data["csv_file"]
        try:
            batch = stage_csv_import(
                upload,
                series=form.cleaned_data["series"],
                source=form.cleaned_data["source"],
                uploaded_by=request.user,
                filename=upload.name,
            )
        except ValidationError as exc:
            form.add_error("csv_file", _validation_message(exc))
        else:
            messages.success(request, "CSV staged. No row is public until a verifier approves it.")
            return redirect("prices:import-detail", public_id=batch.public_id)
    return render(request, "prices/import_form.html", {"form": form})


@login_required
def import_detail(request, public_id):
    _assert_management_access(request.user)
    batch = get_object_or_404(
        ImportBatch.objects.select_related("series", "source", "uploaded_by"),
        public_id=public_id,
    )
    rows = batch.rows.select_related("observation", "reviewed_by")
    return render(
        request,
        "prices/import_detail.html",
        {
            "batch": batch,
            "rows": rows[:500],
            "verification_form": VerificationForm(),
            "rejection_form": RejectionForm(),
            "can_verify": can_verify(request.user),
            "is_own": batch.uploaded_by_id == request.user.pk,
        },
    )


@require_POST
@verifier_required
def import_row_approve(request, public_id, row_number):
    row = get_object_or_404(
        ImportRow.objects.select_related("batch"),
        batch__public_id=public_id,
        row_number=row_number,
    )
    form = VerificationForm(request.POST)
    if form.is_valid():
        try:
            approve_import_row(
                row,
                verifier=request.user,
                reason=form.cleaned_data["reason"],
                allow_outlier=form.cleaned_data["accept_outlier"],
                allow_conflict=form.cleaned_data["accept_conflict"],
            )
        except ValidationError as exc:
            messages.error(request, _validation_message(exc))
        else:
            messages.success(request, f"Row {row_number} approved.")
    return redirect("prices:import-detail", public_id=public_id)


@require_POST
@verifier_required
def import_row_reject(request, public_id, row_number):
    row = get_object_or_404(
        ImportRow.objects.select_related("batch"),
        batch__public_id=public_id,
        row_number=row_number,
    )
    form = RejectionForm(request.POST)
    if form.is_valid():
        try:
            reject_import_row(row, verifier=request.user, reason=form.cleaned_data["reason"])
        except ValidationError as exc:
            messages.error(request, _validation_message(exc))
        else:
            messages.success(request, f"Row {row_number} rejected.")
    else:
        messages.error(request, "A rejection reason is required.")
    return redirect("prices:import-detail", public_id=public_id)


@login_required
def import_errors(request, public_id):
    _assert_management_access(request.user)
    batch = get_object_or_404(ImportBatch, public_id=public_id)
    response = HttpResponse(import_error_report(batch), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        f'attachment; filename="gabay-import-{batch.public_id}-errors.csv"'
    )
    return response


@login_required
def csv_template(request):
    _assert_management_access(request.user)
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(sorted(REQUIRED_HEADERS))
    response = HttpResponse(output.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="gabay-price-import-template.csv"'
    return response
