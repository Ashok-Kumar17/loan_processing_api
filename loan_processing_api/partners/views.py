"""Partner (MFD/RIA) dashboard.

Every endpoint here runs in a fixed number of SQL queries no matter how many
clients, links or accrual rows a partner has -- aggregation happens in the
database (annotate / conditional Sum / Subquery), never in Python loops. The
tests pin those query counts with django_assert_num_queries.
"""

from django.db.models import Count, DecimalField, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce, TruncMonth
from django.utils import timezone
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import generics, mixins, serializers, viewsets
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import User
from accounts.permissions import IsPartner
from lending.models import CreditLine

from .models import CommissionAccrual, ReferralLink
from .serializers import ClientSerializer, MonthlyCommissionSerializer, ReferralLinkSerializer

MONEY = DecimalField(max_digits=16, decimal_places=2)
ZERO = Value(0, output_field=MONEY)
OPEN = list(CreditLine.OPEN_STATUSES)


class ReferralLinkViewSet(
    mixins.ListModelMixin, mixins.CreateModelMixin, mixins.UpdateModelMixin, viewsets.GenericViewSet
):
    """Create campaign-specific referral links and see sign-ups per link."""

    permission_classes = [IsPartner]
    serializer_class = ReferralLinkSerializer
    http_method_names = ["get", "post", "patch"]

    def get_queryset(self):
        return (
            ReferralLink.objects.filter(partner=self.request.user.partner)
            .annotate(
                signup_count=Count("signups", distinct=True),
                active_line_count=Count(
                    "signups__credit_lines",
                    filter=Q(signups__credit_lines__status=CreditLine.Status.ACTIVE),
                    distinct=True,
                ),
            )
            .order_by("-created_at", "-id")
        )

    def perform_create(self, serializer):
        serializer.save(partner=self.request.user.partner)


class DashboardView(APIView):
    permission_classes = [IsPartner]

    @extend_schema(
        responses=inline_serializer(
            "PartnerDashboard",
            {
                "partner": serializers.CharField(),
                "clients": serializers.IntegerField(),
                "active_lines": serializers.IntegerField(),
                "sanctioned_total": serializers.DecimalField(16, 2),
                "outstanding_total": serializers.DecimalField(16, 2),
                "commission_this_month": serializers.DecimalField(16, 2),
                "commission_lifetime": serializers.DecimalField(16, 2),
            },
        )
    )
    def get(self, request):
        partner = request.user.partner
        month_start = timezone.localdate().replace(day=1)

        clients = User.objects.filter(referred_via__partner=partner).count()
        lines = CreditLine.objects.filter(partner=partner, status=CreditLine.Status.ACTIVE).aggregate(
            active_lines=Count("id"),
            sanctioned_total=Coalesce(Sum("sanctioned_limit"), ZERO),
            outstanding_total=Coalesce(Sum("outstanding_principal"), ZERO),
        )
        commission = CommissionAccrual.objects.filter(partner=partner).aggregate(
            lifetime=Coalesce(Sum("amount"), ZERO),
            this_month=Coalesce(Sum("amount", filter=Q(accrual_date__gte=month_start)), ZERO),
        )
        return Response(
            {
                "partner": partner.firm_name,
                "clients": clients,
                "active_lines": lines["active_lines"],
                "sanctioned_total": f"{lines['sanctioned_total']:.2f}",
                "outstanding_total": f"{lines['outstanding_total']:.2f}",
                "commission_this_month": f"{commission['this_month']:.2f}",
                "commission_lifetime": f"{commission['lifetime']:.2f}",
            }
        )


class ClientListView(generics.ListAPIView):
    """Referred clients with their open line and commission earned -- paginated, one query per page."""

    permission_classes = [IsPartner]
    serializer_class = ClientSerializer

    def get_queryset(self):
        partner = self.request.user.partner
        open_line = CreditLine.objects.filter(borrower=OuterRef("pk"), status__in=OPEN).order_by("-id")
        earned = (
            CommissionAccrual.objects.filter(partner=partner, credit_line__borrower=OuterRef("pk"))
            .values("credit_line__borrower")
            .annotate(total=Sum("amount"))
            .values("total")
        )
        return (
            User.objects.filter(referred_via__partner=partner)
            .select_related("referred_via")
            .annotate(
                line_status=Subquery(open_line.values("status")[:1]),
                sanctioned_limit=Coalesce(Subquery(open_line.values("sanctioned_limit")[:1]), ZERO),
                outstanding=Coalesce(Subquery(open_line.values("outstanding_principal")[:1]), ZERO),
                commission_earned=Coalesce(Subquery(earned, output_field=MONEY), ZERO),
            )
            .order_by("-date_joined", "-id")
        )


class CommissionHistoryView(APIView):
    permission_classes = [IsPartner]

    @extend_schema(responses=MonthlyCommissionSerializer(many=True))
    def get(self, request):
        rows = (
            CommissionAccrual.objects.filter(partner=request.user.partner)
            .annotate(month=TruncMonth("accrual_date"))
            .values("month")
            .annotate(amount=Sum("amount"), lines=Count("credit_line", distinct=True))
            .order_by("-month")
        )
        return Response(MonthlyCommissionSerializer(rows, many=True).data)
