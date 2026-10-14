from decimal import Decimal

from django.db.models import Prefetch
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsBorrower, IsOps
from core.idempotency import run_idempotent

from . import services
from .models import CreditLine, Holding, LedgerEntry, Pledge
from .serializers import (
    AccrualRequestSerializer,
    AmountSerializer,
    CreditLineCreateSerializer,
    CreditLineSerializer,
    HoldingSerializer,
    LedgerEntrySerializer,
)

IDEMPOTENCY_HEADER = OpenApiParameter(
    "Idempotency-Key",
    str,
    OpenApiParameter.HEADER,
    required=True,
    description="Unique per logical operation (e.g. a UUID). Retries with the same key never move money twice.",
)


class PortfolioViewSet(viewsets.GenericViewSet):
    permission_classes = [IsBorrower]
    serializer_class = HoldingSerializer

    def get_queryset(self):
        return Holding.objects.filter(borrower=self.request.user)

    def _summary(self, holdings):
        data = HoldingSerializer(holdings, many=True).data
        return {
            "holdings": data,
            "total_market_value": str(sum((h.market_value for h in holdings), Decimal("0.00"))),
            "total_eligible_value": str(sum((h.eligible_value for h in holdings), Decimal("0.00"))),
        }

    def list(self, request):
        return Response(self._summary(list(self.get_queryset())))

    @extend_schema(request=None)
    @action(detail=False, methods=["post"])
    def sync(self, request):
        """Fetch holdings from CAMS and KFintech using the borrower's PAN."""
        return Response(self._summary(services.sync_portfolio(request.user)))


class CreditLineViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = CreditLineSerializer

    def get_permissions(self):
        if self.action in ("list", "retrieve", "statement"):
            return [(IsBorrower | IsOps)()]
        return [IsBorrower()]

    def get_queryset(self):
        qs = CreditLine.objects.prefetch_related(Prefetch("pledges", queryset=Pledge.objects.select_related("holding")))
        if self.request.user.is_ops:
            return qs
        return qs.filter(borrower=self.request.user)

    @extend_schema(request=CreditLineCreateSerializer, responses={201: CreditLineSerializer})
    def create(self, request):
        """Apply for a credit line against selected holdings. Runs the bureau check immediately."""
        payload = CreditLineCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        line = services.apply_for_credit_line(
            request.user,
            payload.validated_data["requested_limit"],
            [services.PledgeRequest(p["holding"], p["units"]) for p in payload.validated_data["pledges"]],
        )
        return Response(CreditLineSerializer(self.get_queryset().get(pk=line.pk)).data, status=status.HTTP_201_CREATED)

    def _line_response(self, line):
        return Response(CreditLineSerializer(self.get_queryset().get(pk=line.pk)).data)

    @extend_schema(request=None)
    @action(detail=True, methods=["post"])
    def pledge(self, request, pk=None):
        """Mark liens with the RTAs on all pledged units and activate the line."""
        return self._line_response(services.mark_liens(request.user, int(pk)))

    @extend_schema(request=None)
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        return self._line_response(services.cancel_line(request.user, int(pk)))

    @extend_schema(request=None)
    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        """Close a fully repaid line and release all liens."""
        return self._line_response(services.close_line(request.user, int(pk)))

    def _money_action(self, request, pk, scope, fn):
        payload = AmountSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        def handler():
            entry = fn(request.user, int(pk), payload.validated_data["amount"])
            return status.HTTP_201_CREATED, LedgerEntrySerializer(entry).data

        return run_idempotent(request, scope=f"{scope}:{pk}", handler=handler)

    @extend_schema(request=AmountSerializer, responses={201: LedgerEntrySerializer}, parameters=[IDEMPOTENCY_HEADER])
    @action(detail=True, methods=["post"])
    def drawdowns(self, request, pk=None):
        """Withdraw money from the credit line to the borrower's bank account."""
        return self._money_action(request, pk, "drawdown", services.drawdown)

    @extend_schema(request=AmountSerializer, responses={201: LedgerEntrySerializer}, parameters=[IDEMPOTENCY_HEADER])
    @action(detail=True, methods=["post"])
    def repayments(self, request, pk=None):
        """Repay; interest due is settled before principal."""
        return self._money_action(request, pk, "repayment", services.repay)

    @extend_schema(responses=LedgerEntrySerializer(many=True))
    @action(detail=True, methods=["get"])
    def statement(self, request, pk=None):
        line = self.get_object()
        page = self.paginate_queryset(LedgerEntry.objects.filter(credit_line=line).order_by("id"))
        return self.get_paginated_response(LedgerEntrySerializer(page, many=True).data)


class AccrueInterestView(APIView):
    """Ops: run the daily interest + trail-commission accrual for a date (safe to re-run)."""

    permission_classes = [IsOps]

    @extend_schema(request=AccrualRequestSerializer)
    def post(self, request):
        payload = AccrualRequestSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        result = services.accrue_for_day(payload.validated_data["date"])
        return Response(
            {
                "date": result.day,
                "lines": result.lines,
                "interest": str(result.interest),
                "commission": str(result.commission),
            }
        )
