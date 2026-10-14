from rest_framework.permissions import BasePermission


class IsBorrower(BasePermission):
    message = "Only borrower accounts can do this."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_borrower)


class IsPartner(BasePermission):
    message = "Only partner (MFD/RIA) accounts can do this."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_partner and hasattr(user, "partner"))


class IsOps(BasePermission):
    message = "Only operations staff can do this."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_ops)
