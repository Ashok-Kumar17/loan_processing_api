from drf_spectacular.utils import extend_schema
from rest_framework import generics, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from .serializers import MeSerializer, RegisterSerializer


class RegisterView(generics.GenericAPIView):
    """Borrower sign-up. Pass `referral_code` to attribute the borrower to a partner."""

    permission_classes = [AllowAny]
    authentication_classes = []
    serializer_class = RegisterSerializer
    throttle_scope = "auth"

    @extend_schema(responses={201: MeSerializer})
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(MeSerializer(user).data, status=status.HTTP_201_CREATED)


class ThrottledTokenObtainPairView(TokenObtainPairView):
    throttle_scope = "auth"


class MeView(generics.RetrieveAPIView):
    serializer_class = MeSerializer

    def get_object(self):
        return self.request.user


__all__ = ["RegisterView", "ThrottledTokenObtainPairView", "TokenRefreshView", "MeView"]
