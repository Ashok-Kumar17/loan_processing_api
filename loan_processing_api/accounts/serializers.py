from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from core import crypto
from partners.models import ReferralLink

from .models import User, validate_pan


class RegisterSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(write_only=True, min_length=8)
    email = serializers.EmailField(required=False, allow_blank=True)
    phone = serializers.RegexField(r"^[6-9]\d{9}$", required=False, allow_blank=True)
    pan = serializers.CharField(write_only=True)
    referral_code = serializers.CharField(required=False, allow_blank=True, write_only=True)

    def validate_username(self, value):
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("Username is taken.")
        return value

    def validate_pan(self, value):
        try:
            pan = validate_pan(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages) from exc
        if User.objects.filter(pan_index=crypto.blind_index(pan)).exists():
            raise serializers.ValidationError("An account with this PAN already exists.")
        return pan

    def validate_referral_code(self, value):
        if not value:
            return None
        link = ReferralLink.objects.filter(code=value, is_active=True).first()
        if link is None:
            raise serializers.ValidationError("Unknown or inactive referral code.")
        return link

    def validate(self, attrs):
        try:
            password_validation.validate_password(attrs["password"], User(username=attrs["username"]))
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": exc.messages}) from exc
        return attrs

    def create(self, validated_data):
        user = User(
            username=validated_data["username"],
            email=validated_data.get("email", ""),
            phone=validated_data.get("phone", ""),
            role=User.Role.BORROWER,
            referred_via=validated_data.get("referral_code"),
        )
        user.set_password(validated_data["password"])
        user.set_pan(validated_data["pan"])
        user.save()
        return user


class MeSerializer(serializers.ModelSerializer):
    pan_masked = serializers.CharField(read_only=True)

    class Meta:
        model = User
        fields = ["id", "username", "email", "phone", "role", "pan_masked", "date_joined"]
        read_only_fields = fields
