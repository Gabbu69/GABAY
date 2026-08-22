from __future__ import annotations

from django.conf import settings

UI_COPY = {
    "en": {
        "skip": "Skip to main content",
        "home": "Home",
        "history": "Price history",
        "check_offer": "Check my offer",
        "methodology": "How GABAY works",
        "management": "Management",
        "login": "Staff sign in",
        "logout": "Sign out",
        "menu": "Open navigation",
        "language": "Wika: Filipino",
        "language_code": "tl",
        "brand_tagline": "Palay price guidance for M’lang",
        "demo_banner": "Demonstration data — not verified market information.",
        "offline_banner": "You are offline. Showing the last saved public information.",
        "stale": "Stale data",
        "offline": "Offline copy",
        "live": "Connected",
        "verified": "Verified local data",
        "reference": "Provincial reference",
        "demo": "Demonstration",
        "learn_more": "Read the methodology",
        "privacy_note": "No names, farm locations, or offer values are stored here.",
        "disclaimer": (
            "GABAY is decision support, not a promise of income or a direction to trade. "
            "Always consider your own costs, quality, buyer terms, and urgent needs."
        ),
        "footer_location": "Designed for palay decisions in M’lang, Cotabato",
        "data_status": "Data status",
        "updated": "Updated",
        "source": "Source",
        "records": "observations",
        "no_data": "No verified local price is available yet.",
        "php_kg": "PHP/kg",
        "reliability": "Reliability",
        "forecast": "Next-week forecast",
        "current_price": "Current weekly price",
        "uncertainty": "Expected range",
        "recommendation": "GABAY result",
        "monitor": "MONITOR",
        "sell": "SELL",
        "wait": "WAIT",
        "unassessed": "UNASSESSED",
        "why": "Why this result",
        "not_probability": "Reliability describes past forecast error, not a success probability.",
    },
    "tl": {
        "skip": "Lumaktaw sa pangunahing nilalaman",
        "home": "Tahanan",
        "history": "Kasaysayan ng presyo",
        "check_offer": "Suriin ang alok ko",
        "methodology": "Paano gumagana ang GABAY",
        "management": "Pamamahala",
        "login": "Mag-sign in ang kawani",
        "logout": "Mag-sign out",
        "menu": "Buksan ang nabigasyon",
        "language": "Language: English",
        "language_code": "en",
        "brand_tagline": "Gabay sa presyo ng palay para sa M’lang",
        "demo_banner": "Pang-demonstrasyong datos — hindi beripikadong impormasyon sa merkado.",
        "offline_banner": (
            "Walang internet. Ipinapakita ang huling na-save na pampublikong impormasyon."
        ),
        "stale": "Luma na ang datos",
        "offline": "Naka-save na kopya",
        "live": "Nakakonekta",
        "verified": "Beripikadong lokal na datos",
        "reference": "Sanggunian ng probinsya",
        "demo": "Demonstrasyon",
        "learn_more": "Basahin ang pamamaraan",
        "privacy_note": "Walang pangalan, lokasyon ng bukid, o halaga ng alok na sine-save rito.",
        "disclaimer": (
            "Ang GABAY ay suporta sa pagpapasya, hindi pangako ng kita o utos na magbenta. "
            "Isaalang-alang lagi ang gastos, kalidad, kondisyon ng mamimili, "
            "at agarang pangangailangan."
        ),
        "footer_location": "Dinisenyo para sa pagpapasya sa palay sa M’lang, Cotabato",
        "data_status": "Kalagayan ng datos",
        "updated": "Huling update",
        "source": "Pinagmulan",
        "records": "obserbasyon",
        "no_data": "Wala pang beripikadong lokal na presyo.",
        "php_kg": "PHP/kg",
        "reliability": "Pagiging maaasahan",
        "forecast": "Taya para sa susunod na linggo",
        "current_price": "Kasalukuyang lingguhang presyo",
        "uncertainty": "Inaasahang saklaw",
        "recommendation": "Resulta ng GABAY",
        "monitor": "SUBAYBAYAN",
        "sell": "IBENTA",
        "wait": "MAGHINTAY",
        "unassessed": "DI PA NASUSURI",
        "why": "Bakit ito ang resulta",
        "not_probability": (
            "Ang pagiging maaasahan ay batay sa nakaraang error, hindi posibilidad ng tagumpay."
        ),
    },
}


def language_for(request) -> str:
    language = request.session.get("gabay_language")
    if language not in UI_COPY:
        language = getattr(request, "LANGUAGE_CODE", settings.LANGUAGE_CODE).split("-")[0]
    return language if language in UI_COPY else "en"


def site_context(request):
    language = language_for(request)
    return {
        "gabay_profile": settings.PROFILE,
        "is_demo_profile": settings.PROFILE in {"development", "demo"},
        "ui_language": language,
        "ui": UI_COPY[language],
    }
