from django.conf import settings
from rest_framework import serializers

from .models import ReferralLink


class ReferralLinkSerializer(serializers.ModelSerializer):
    share_url = serializers.SerializerMethodField()
    signups = serializers.IntegerField(source="signup_count", read_only=True, default=0)
    active_lines = serializers.IntegerField(source="active_line_count", read_only=True, default=0)

    class Meta:
        model = ReferralLink
        fields = ["id", "code", "label", "is_active", "share_url", "signups", "active_lines", "created_at"]
        read_only_fields = ["id", "code", "share_url", "signups", "active_lines", "created_at"]

    def get_share_url(self, obj) -> str:
        return settings.PARTNER_SIGNUP_URL.format(code=obj.code)


class ClientSerializer(serializers.Serializer):
    """What a partner may see about a referred client: no PAN, no contact details."""

    id = serializers.IntegerField()
    username = serializers.CharField()
    joined = serializers.DateTimeField(source="date_joined")
    referral_code = serializers.CharField(source="referred_via.code")
    line_status = serializers.CharField(allow_null=True)
    sanctioned_limit = serializers.DecimalField(max_digits=14, decimal_places=2)
    outstanding = serializers.DecimalField(max_digits=14, decimal_places=2)
    commission_earned = serializers.DecimalField(max_digits=14, decimal_places=2)


class MonthlyCommissionSerializer(serializers.Serializer):
    month = serializers.DateField()
    amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    lines = serializers.IntegerField()
