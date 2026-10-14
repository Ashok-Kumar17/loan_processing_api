from decimal import Decimal

from rest_framework import serializers

from .models import CreditLine, Holding, LedgerEntry, Pledge


class HoldingSerializer(serializers.ModelSerializer):
    free_units = serializers.DecimalField(max_digits=18, decimal_places=3, read_only=True)
    ltv = serializers.DecimalField(max_digits=4, decimal_places=2, read_only=True)
    market_value = serializers.DecimalField(max_digits=16, decimal_places=2, read_only=True)
    eligible_value = serializers.DecimalField(max_digits=16, decimal_places=2, read_only=True)

    class Meta:
        model = Holding
        fields = [
            "id",
            "rta",
            "folio",
            "isin",
            "scheme_name",
            "category",
            "units",
            "pledged_units",
            "free_units",
            "nav",
            "nav_date",
            "ltv",
            "market_value",
            "eligible_value",
        ]
        read_only_fields = fields


class PledgeInputSerializer(serializers.Serializer):
    holding = serializers.IntegerField(min_value=1)
    units = serializers.DecimalField(max_digits=18, decimal_places=3, min_value=Decimal("0.001"))


class CreditLineCreateSerializer(serializers.Serializer):
    requested_limit = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("1"))
    pledges = PledgeInputSerializer(many=True, allow_empty=False)


class PledgeSerializer(serializers.ModelSerializer):
    scheme_name = serializers.CharField(source="holding.scheme_name", read_only=True)
    rta = serializers.CharField(source="holding.rta", read_only=True)

    class Meta:
        model = Pledge
        fields = [
            "id",
            "holding",
            "scheme_name",
            "rta",
            "units",
            "nav_at_pledge",
            "ltv",
            "eligible_value",
            "status",
            "lien_reference",
        ]
        read_only_fields = fields


class CreditLineSerializer(serializers.ModelSerializer):
    available_limit = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    total_due = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    pledges = PledgeSerializer(many=True, read_only=True)

    class Meta:
        model = CreditLine
        fields = [
            "id",
            "status",
            "requested_limit",
            "sanctioned_limit",
            "available_limit",
            "apr",
            "bureau_score",
            "outstanding_principal",
            "interest_due",
            "total_due",
            "rejection_reason",
            "created_at",
            "activated_at",
            "closed_at",
            "pledges",
        ]
        read_only_fields = fields


class AmountSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("1.00"))


class LedgerEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = LedgerEntry
        fields = [
            "id",
            "entry_type",
            "amount",
            "principal_component",
            "interest_component",
            "balance_after",
            "reference",
            "value_date",
            "created_at",
        ]
        read_only_fields = fields


class AccrualRequestSerializer(serializers.Serializer):
    date = serializers.DateField()
