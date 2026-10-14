from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import ClientListView, CommissionHistoryView, DashboardView, ReferralLinkViewSet

router = DefaultRouter()
router.register("referral-links", ReferralLinkViewSet, basename="referral-link")

urlpatterns = [
    path("", include(router.urls)),
    path("dashboard/", DashboardView.as_view(), name="partner-dashboard-api"),
    path("clients/", ClientListView.as_view(), name="partner-clients"),
    path("commissions/", CommissionHistoryView.as_view(), name="partner-commissions"),
]
