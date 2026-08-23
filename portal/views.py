from __future__ import annotations

import logging
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST

from portal.context_processors import language_for
from portal.forms import OfferCheckForm

logger = logging.getLogger(__name__)

REASON_COPY = {
    "en": {
        "FORECAST_UP": "The published forecast is above the current weekly price.",
        "FORECAST_DOWN": "The published forecast is below the current weekly price.",
        "FORECAST_FLAT": "The forecast change is within the neutral band.",
        "TREND_UP": "The recent two-week price trend is rising.",
        "TREND_DOWN": "The recent two-week price trend is falling.",
        "PRICE_LOW": "The current price is low compared with the previous eight weeks.",
        "PRICE_HIGH": "The current price is high compared with the previous eight weeks.",
        "STALE_DATA": "The latest verified observation is too old for a directional result.",
        "INSUFFICIENT_DATA": "There is not enough comparable verified history yet.",
        "LOW_RELIABILITY": "Recent forecast errors are too large for a directional result.",
        "WIDE_INTERVAL": "The forecast range is too wide for a directional result.",
        "MISSING_INPUT": "A required input is unavailable.",
        "CONFLICTING_SIGNALS": "Price signals do not agree, so monitoring is safer.",
        "RULE_PROTOCOL_UNAPPROVED": (
            "The decision rules are awaiting adviser approval, so no directional advice is shown."
        ),
        "TARGET_WEEK_MISMATCH": "The published forecast is not for the current or upcoming week.",
        "CURRENT_EVIDENCE_CHANGED": (
            "Verified public evidence changed after this forecast was locked."
        ),
    },
    "tl": {
        "FORECAST_UP": "Mas mataas ang inilathalang taya kaysa kasalukuyang lingguhang presyo.",
        "FORECAST_DOWN": "Mas mababa ang inilathalang taya kaysa kasalukuyang lingguhang presyo.",
        "FORECAST_FLAT": "Nasa neutral na saklaw ang pagbabago ng taya.",
        "TREND_UP": "Tumataas ang takbo ng presyo sa huling dalawang linggo.",
        "TREND_DOWN": "Bumababa ang takbo ng presyo sa huling dalawang linggo.",
        "PRICE_LOW": "Mababa ang presyo kumpara sa nakaraang walong linggo.",
        "PRICE_HIGH": "Mataas ang presyo kumpara sa nakaraang walong linggo.",
        "STALE_DATA": "Masyado nang luma ang huling beripikadong obserbasyon.",
        "INSUFFICIENT_DATA": "Kulang pa ang maihahambing na beripikadong kasaysayan.",
        "LOW_RELIABILITY": "Masyadong malaki ang kamakailang error ng taya.",
        "WIDE_INTERVAL": "Masyadong malawak ang saklaw ng taya.",
        "MISSING_INPUT": "May kinakailangang datos na hindi makuha.",
        "CONFLICTING_SIGNALS": "Hindi nagtutugma ang mga senyales kaya mas ligtas magmasid.",
        "RULE_PROTOCOL_UNAPPROVED": (
            "Hinihintay pa ang pag-apruba ng tagapayo sa mga tuntunin "
            "kaya walang direksiyonal na payo."
        ),
        "TARGET_WEEK_MISMATCH": (
            "Ang inilathalang taya ay hindi para sa kasalukuyan o susunod na linggo."
        ),
        "CURRENT_EVIDENCE_CHANGED": (
            "Nagbago ang beripikadong pampublikong ebidensya matapos i-lock ang tayang ito."
        ),
    },
}


def _management_access(user) -> tuple[bool, bool]:
    """Return price/research access without granting rights from mere authentication."""

    if not getattr(user, "is_authenticated", False):
        return False, False
    from forecasting.services import user_can_run_experiments
    from prices.permissions import can_encode, can_verify

    price_access = bool(user.is_superuser or can_encode(user) or can_verify(user))
    research_access = bool(user_can_run_experiments(user))
    return price_access, research_access


def _read(value, key: str, default=None):
    if value is None:
        return default
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _decimal(value):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _normalise_source(source) -> dict:
    if not source:
        return {
            "name": "Not available",
            "type": "",
            "reference": "",
            "citation": "",
            "location_name": "",
        }
    return {
        "name": (
            _read(source, "public_name")
            or _read(source, "display_name")
            or _read(source, "name")
            or str(source)
        ),
        "type": _read(source, "source_type", ""),
        "reference": (
            _read(source, "reference_url", "")
            or _read(source, "reference", "")
            or _read(source, "url", "")
        ),
        "citation": _read(source, "citation", ""),
        "location_name": _read(source, "location_name", ""),
    }


def _normalise_week(item) -> dict:
    sources = [_normalise_source(source) for source in (_read(item, "sources", []) or [])]
    return {
        "week_start": _read(item, "week_start"),
        "week_end": _read(item, "week_end"),
        "price": _decimal(_read(item, "price")),
        "low": _decimal(_read(item, "low")),
        "high": _decimal(_read(item, "high")),
        "count": _read(item, "count", 0) or 0,
        "latest_observed_on": _read(item, "latest_observed_on"),
        "sources": sources,
        "source_count": _read(item, "source_count", len(sources)) or len(sources),
    }


def _normalise_section(section) -> dict:
    section = section or {}
    history = [_normalise_week(item) for item in (_read(section, "history", []) or [])]
    history = [item for item in history if item["price"] is not None]
    latest = _normalise_week(_read(section, "latest")) if _read(section, "latest") else None
    sources = [_normalise_source(source) for source in (_read(section, "sources", []) or [])]
    legacy_source = _read(section, "source")
    if legacy_source and not sources:
        sources = [_normalise_source(legacy_source)]
    source = sources[0] if len(sources) == 1 else _normalise_source(legacy_source)
    series = _read(section, "series")
    metadata = _read(section, "series_metadata", {}) or {}
    return {
        "available": bool(_read(section, "available", latest or history)),
        "series": series,
        "series_metadata": {
            "name": _read(metadata, "name", _read(series, "name", "")),
            "slug": _read(metadata, "slug", _read(series, "slug", "")),
            "location_name": _read(
                metadata, "location_name", _read(series, "location_name", "")
            ),
            "classification_label": _read(
                metadata,
                "classification_label",
                _read(metadata, "classification", _read(series, "classification", "")),
            ),
            "condition_label": _read(
                metadata,
                "condition_label",
                _read(metadata, "condition", _read(series, "condition", "")),
            ),
            "moisture_basis": _read(
                metadata, "moisture_basis", _read(series, "moisture_basis")
            ),
            "canonical_unit": _read(
                metadata, "canonical_unit", _read(series, "canonical_unit", "PHP/kg")
            ),
        },
        "latest": latest,
        "history": history,
        "source": source,
        "sources": sources,
        "source_count": len(sources),
        "selection_status": _read(section, "selection_status", ""),
    }


def _fetch_price_context():
    try:
        from prices.services import get_public_price_context

        return get_public_price_context(history_weeks=104)
    except Exception:
        logger.warning("Public price context is unavailable", exc_info=True)
        return None


def _price_context() -> dict:
    raw = _fetch_price_context() or {}
    sections = {
        name: _normalise_section(_read(raw, name, {}))
        for name in ("local", "reference", "demo")
    }
    selected_scope = str(_read(raw, "selected_scope", "local") or "local").lower()
    if selected_scope not in sections:
        selected_scope = "local"
    selected = sections[selected_scope]
    top_latest = _read(raw, "latest")
    top_history = _read(raw, "history")
    if top_latest or top_history:
        selected = _normalise_section(
            {
                "available": bool(top_latest or top_history),
                "series": _read(raw, "series"),
                "latest": top_latest,
                "history": top_history or [],
                "source": _read(raw, "source"),
                "sources": _read(raw, "sources", []),
                "series_metadata": _read(raw, "series_metadata", {}),
                "selection_status": "selected_primary",
            }
        )
        sections[selected_scope] = selected
    return {
        "selected_scope": selected_scope,
        "selected": selected,
        "sections": sections,
        "has_verified_local_data": bool(_read(raw, "has_verified_local_data", False)),
        "is_demo": bool(_read(raw, "is_demo", selected_scope == "demo")),
        "is_stale": bool(_read(raw, "is_stale", False)),
        "data_age_days": _read(raw, "data_age_days"),
        "notice": _read(raw, "notice", ""),
    }


def _fetch_forecast_context():
    try:
        from forecasting.services import get_public_forecast_context

        return get_public_forecast_context()
    except Exception:
        logger.warning("Public forecast context is unavailable", exc_info=True)
        return None


def _fetch_offer_evaluation(offer_price: Decimal):
    from forecasting.services import evaluate_offer

    return evaluate_offer(offer_price)


def _forecast_context(raw, request: HttpRequest, *, current_price=None) -> dict:
    raw = raw or {}
    language = language_for(request)
    decision = str(_read(raw, "decision", "MONITOR") or "MONITOR").upper()
    reliability = str(_read(raw, "reliability", "UNASSESSED") or "UNASSESSED").upper()
    reason_codes = list(_read(raw, "reason_codes", []) or [])
    reasons = [
        REASON_COPY[language].get(code, str(code).replace("_", " ").title())
        for code in reason_codes
    ]
    recommendation = _read(raw, "recommendation")
    rule_set = _read(recommendation, "rule_set")
    messages = _read(raw, "messages", {}) or _read(raw, "explanations", {}) or {}
    message = _read(messages, language, "")
    if not message and language == "en":
        message = _read(raw, "message", "")
    if not message and language == "tl" and reasons:
        message = reasons[0]
    if not reasons and message:
        reasons = [message]
    return {
        "available": bool(_read(raw, "available", False)),
        "status": _read(raw, "status", "insufficient"),
        "decision": decision if decision in {"SELL", "WAIT", "MONITOR"} else "MONITOR",
        "reliability": reliability,
        "point_forecast": _decimal(_read(raw, "point_forecast")),
        "interval_lower": _decimal(_read(raw, "interval_lower")),
        "interval_upper": _decimal(_read(raw, "interval_upper")),
        "target_week": _read(raw, "target_week"),
        "current_price": _decimal(_read(raw, "current_price")) or current_price,
        "data_age_days": _read(raw, "data_age_days"),
        "reasons": reasons,
        "message": message,
        "is_demo": bool(_read(raw, "is_demo", False)),
        "offer_price": _decimal(_read(raw, "offer_price")),
        "rule_version": _read(raw, "rule_version", _read(rule_set, "version", "")),
        "rule_status": _read(raw, "rule_status", _read(rule_set, "status", "")),
    }


def _chart_context(history: list[dict], *, forecast: dict | None = None) -> dict:
    clean = [item for item in history if item["price"] is not None]
    values = [float(item["price"]) for item in clean]
    if not values:
        return {
            "points": "",
            "minimum": None,
            "maximum": None,
            "count": 0,
            "last": None,
            "marker": None,
            "ticks": [],
        }
    point = _decimal(_read(forecast, "point_forecast")) if forecast else None
    lower = _decimal(_read(forecast, "interval_lower")) if forecast else None
    upper = _decimal(_read(forecast, "interval_upper")) if forecast else None

    plotted = values[:]
    if point is not None:
        plotted.append(float(point))
    bounds = plotted[:]
    if lower is not None:
        bounds.append(float(lower))
    if upper is not None:
        bounds.append(float(upper))
    minimum = min(bounds)
    maximum = max(bounds)
    spread = maximum - minimum or 1
    width, height, padding = 720, 220, 20
    denominator = max(len(plotted) - 1, 1)

    def project(value: float, index: int) -> tuple[str, str]:
        x = padding + ((width - padding * 2) * index / denominator)
        y = height - padding - ((height - padding * 2) * (value - minimum) / spread)
        return f"{x:.1f}", f"{y:.1f}"

    projected = [project(value, index) for index, value in enumerate(plotted)]
    history_points = projected[:-1] if point is not None else projected
    line_points = " ".join(f"{x},{y}" for x, y in history_points)

    last_x, last_y = projected[len(values) - 1]
    last = {"x": last_x, "y": last_y}

    marker = None
    if point is not None:
        marker_x, marker_y = projected[-1]
        marker = {"x": marker_x, "y": marker_y, "band_y": "", "band_h": 0}
        if lower is not None and upper is not None:
            low_y = float(project(float(lower), len(plotted) - 1)[1])
            high_y = float(project(float(upper), len(plotted) - 1)[1])
            marker["band_y"] = f"{min(low_y, high_y):.1f}"
            marker["band_h"] = f"{abs(high_y - low_y):.1f}"

    step = max(len(clean) // 5, 1)
    ticks = []
    for start in range(0, len(clean), step)[:6]:
        item = clean[start]
        x, _ = project(float(item["price"]), start)
        week_start = _read(item, "week_start")
        if week_start is not None:
            ticks.append({"x": x, "label": f"{week_start.strftime('%b')} {week_start.day}"})

    return {
        "points": line_points,
        "minimum": Decimal(str(minimum)),
        "maximum": Decimal(str(maximum)),
        "count": len(values),
        "last": last,
        "marker": marker,
        "ticks": ticks,
    }


@require_GET
def home(request: HttpRequest) -> HttpResponse:
    prices = _price_context()
    latest = prices["selected"]["latest"]
    current_price = latest["price"] if latest else None
    forecast = _forecast_context(_fetch_forecast_context(), request, current_price=current_price)
    return render(
        request,
        "portal/home.html",
        {
            "prices": prices,
            "forecast": forecast,
            "chart": _chart_context(prices["selected"]["history"][-24:], forecast=forecast),
            "page_is_demo": prices["is_demo"] or forecast["is_demo"],
        },
    )


@require_GET
def history(request: HttpRequest) -> HttpResponse:
    prices = _price_context()
    requested_scope = request.GET.get("scope", prices["selected_scope"]).lower()
    if requested_scope not in prices["sections"]:
        requested_scope = prices["selected_scope"]
    section = prices["sections"][requested_scope]
    return render(
        request,
        "portal/history.html",
        {
            "prices": prices,
            "history_section": section,
            "history_scope": requested_scope,
            "chart": _chart_context(section["history"]),
            "page_is_demo": requested_scope == "demo",
        },
    )


@require_GET
def methodology(request: HttpRequest) -> HttpResponse:
    return render(request, "portal/methodology.html")


def check_offer(request: HttpRequest) -> HttpResponse:
    language = language_for(request)
    prices = _price_context()
    latest = prices["selected"]["latest"]
    current_price = latest["price"] if latest else None
    published_forecast = _forecast_context(
        _fetch_forecast_context(), request, current_price=current_price
    )
    result = None
    evaluation_error = ""
    if request.method == "POST":
        form = OfferCheckForm(request.POST, language=language)
        if form.is_valid():
            try:
                raw_result = _fetch_offer_evaluation(form.cleaned_data["offer_price"])
                result = _forecast_context(raw_result, request, current_price=current_price)
                result["offer_price"] = form.cleaned_data["offer_price"]
            except ValueError as exc:
                form.add_error("offer_price", str(exc))
            except Exception:
                logger.warning("Offer evaluation is unavailable", exc_info=True)
                evaluation_error = (
                    "Hindi pa masuri ang alok. Subukan muli kapag may inilathalang taya."
                    if language == "tl"
                    else (
                        "The offer cannot be evaluated yet. "
                        "Try again after a forecast is published."
                    )
                )
    else:
        form = OfferCheckForm(language=language)
    return render(
        request,
        "portal/check_offer.html",
        {
            "form": form,
            "result": result,
            "evaluation_error": evaluation_error,
            "prices": prices,
            "forecast_rule_version": published_forecast["rule_version"],
            "forecast_rule_status": published_forecast["rule_status"],
            "page_is_demo": prices["is_demo"] or bool(result and result["is_demo"]),
        },
    )


class StaffLoginView(LoginView):
    template_name = "portal/login.html"
    redirect_authenticated_user = True

    def form_valid(self, form):
        if not any(_management_access(form.get_user())):
            form.add_error(None, "This account has no GABAY management role.")
            return self.form_invalid(form)
        return super().form_valid(form)


@login_required
@require_GET
def manage_dashboard(request: HttpRequest) -> HttpResponse:
    price_access, research_access = _management_access(request.user)
    if not (price_access or research_access):
        raise PermissionDenied("A GABAY management role is required.")
    prices = _price_context()
    latest = prices["selected"]["latest"]
    forecast = _forecast_context(
        _fetch_forecast_context(),
        request,
        current_price=latest["price"] if latest else None,
    )
    roles = list(request.user.groups.values_list("name", flat=True))
    return render(
        request,
        "portal/manage_dashboard.html",
        {
            "prices": prices,
            "forecast": forecast,
            "roles": roles,
            "can_manage_prices": price_access,
            "can_manage_research": research_access,
        },
    )


@require_POST
def set_language(request: HttpRequest) -> HttpResponse:
    language = request.POST.get("language", "en")
    if language not in {"en", "tl"}:
        language = "en"
    request.session["gabay_language"] = language
    next_url = request.POST.get("next") or reverse("portal:home")
    if not url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        next_url = reverse("portal:home")
    response = redirect(next_url)
    response.set_cookie("django_language", language, max_age=31_536_000, samesite="Lax")
    return response


@require_GET
def offline(request: HttpRequest) -> HttpResponse:
    return render(request, "portal/offline.html")


@require_GET
def service_worker(request: HttpRequest) -> HttpResponse:
    response = render(request, "portal/service-worker.js", content_type="application/javascript")
    response["Service-Worker-Allowed"] = "/"
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response
