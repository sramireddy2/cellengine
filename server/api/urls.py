from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("datasets", views.DatasetViewSet, basename="dataset")
router.register("runs", views.RunViewSet, basename="run")

urlpatterns = [
    path("auth/login/", views.login_view),
    path("auth/register/", views.register_view),
    path("auth/logout/", views.logout_view),
    path("auth/me/", views.me_view),
    path("", include(router.urls)),
]
