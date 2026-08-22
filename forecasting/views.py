from __future__ import annotations

from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Prefetch
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from .forms import FreezeRuleSetForm, ModelExperimentForm
from .models import DecisionRuleSet, FoldMetric, Forecast, ModelRun
from .services import (
    ExperimentAlreadyRunningError,
    get_or_create_draft_rules,
    run_model_experiment_isolated,
    user_can_run_experiments,
)


def research_admin_required(view):
    @login_required
    @wraps(view)
    def wrapped(request: HttpRequest, *args, **kwargs):
        if not user_can_run_experiments(request.user):
            raise PermissionDenied("Research administrator access is required.")
        return view(request, *args, **kwargs)

    return wrapped


@research_admin_required
def dashboard(request: HttpRequest) -> HttpResponse:
    get_or_create_draft_rules(request.user)
    runs = (
        ModelRun.objects.select_related("snapshot", "snapshot__series", "requested_by")
        .prefetch_related(
            Prefetch("fold_metrics", queryset=FoldMetric.objects.order_by("model_name"))
        )
        .order_by("-created_at")[:20]
    )
    forecasts = Forecast.objects.select_related(
        "series", "model_run", "recommendation", "recommendation__rule_set"
    ).order_by("-published_at")[:20]
    context = {
        "experiment_form": ModelExperimentForm(),
        "runs": runs,
        "forecasts": forecasts,
        "rule_sets": DecisionRuleSet.objects.all(),
        "running": ModelRun.objects.filter(status=ModelRun.Status.RUNNING).exists(),
    }
    return render(request, "forecasting/research_dashboard.html", context)


@research_admin_required
def run_experiment_view(request: HttpRequest) -> HttpResponse:
    if request.method != "POST":
        return redirect("forecasting:dashboard")
    form = ModelExperimentForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Check the series and cutoff, then try again.")
        return render(
            request,
            "forecasting/research_dashboard.html",
            {
                "experiment_form": form,
                "runs": ModelRun.objects.select_related("snapshot", "snapshot__series")[:20],
                "forecasts": Forecast.objects.select_related("series", "model_run")[:20],
                "rule_sets": DecisionRuleSet.objects.all(),
                "running": ModelRun.objects.filter(status=ModelRun.Status.RUNNING).exists(),
            },
            status=400,
        )
    try:
        run = run_model_experiment_isolated(
            form.cleaned_data["series"],
            user=request.user,
            through=form.cleaned_data["through"],
            publish_live=form.cleaned_data["publish_live"],
        )
    except ExperimentAlreadyRunningError as exc:
        messages.warning(request, str(exc))
    except Exception as exc:  # noqa: BLE001 - display a persisted experiment failure
        messages.error(request, f"Experiment failed safely: {exc}")
    else:
        mode = "live ledger" if run.is_live else "simulation ledger"
        messages.success(
            request,
            f"Experiment {run.pk} completed with {run.selected_model} and was saved to the {mode}.",
        )
    return redirect("forecasting:dashboard")


@research_admin_required
def freeze_rule_set(request: HttpRequest) -> HttpResponse:
    if request.method != "POST":
        return redirect("forecasting:dashboard")
    if not (
        request.user.is_superuser or request.user.has_perm("forecasting.freeze_decision_rules")
    ):
        raise PermissionDenied("You do not have permission to freeze the thesis rule protocol.")
    form = FreezeRuleSetForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Rule-set confirmation was invalid.")
        return redirect("forecasting:dashboard")
    rules = get_object_or_404(DecisionRuleSet, pk=form.cleaned_data["rule_set_id"])
    if form.cleaned_data["confirmation"] != rules.version:
        messages.error(request, "The confirmation must exactly match the rule-set version.")
        return redirect("forecasting:dashboard")
    try:
        rules.freeze()
    except Exception as exc:  # noqa: BLE001 - validation is shown without altering the protocol
        messages.error(request, str(exc))
    else:
        messages.success(request, f"{rules.version} is now frozen and immutable.")
    return redirect("forecasting:dashboard")
