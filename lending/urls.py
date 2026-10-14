from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import AccrueInterestView, CreditLineViewSet, PortfolioViewSet

router = DefaultRouter()
router.register("portfolio", PortfolioViewSet, basename="portfolio")
router.register("credit-lines", CreditLineViewSet, basename="credit-line")

urlpatterns = [
    path("", include(router.urls)),
    path("ops/accrue-interest/", AccrueInterestView.as_view(), name="accrue-interest"),
]
