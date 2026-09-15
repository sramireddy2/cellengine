"""Serve the single-page frontend. Phase 3 fills in frontend/index.html."""
from django.conf import settings
from django.http import HttpResponse
from django.urls import path
from django.views.decorators.csrf import ensure_csrf_cookie


@ensure_csrf_cookie
def index(request):
    page = settings.STATICFILES_DIRS[0] / "index.html"
    if not page.exists():
        return HttpResponse("<h1>cellengine</h1><p>Frontend not built yet. API is at /api/.</p>")
    return HttpResponse(page.read_text(encoding="utf-8"))


urlpatterns = [path("", index), path("login/", index)]
