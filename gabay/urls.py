from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path


def health(request):
    return JsonResponse({"status": "ok", "profile": settings.PROFILE})


urlpatterns = [
    path("health/", health, name="health"),
    path("django-admin/", admin.site.urls),
    path("manage/prices/", include("prices.urls")),
    path("manage/research/", include("forecasting.urls")),
    path("", include("portal.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
