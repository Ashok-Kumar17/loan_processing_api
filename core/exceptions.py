from rest_framework import status
from rest_framework.views import exception_handler


class BusinessRuleError(Exception):
    """A request that is well-formed but violates a lending rule (limit exceeded, wrong state...)."""

    def __init__(self, detail: str, code: str = "business_rule", http_status: int = status.HTTP_409_CONFLICT):
        super().__init__(detail)
        self.detail = detail
        self.code = code
        self.http_status = http_status

    def as_body(self) -> dict:
        return {"detail": self.detail, "code": self.code}


def api_exception_handler(exc, context):
    if isinstance(exc, BusinessRuleError):
        from rest_framework.response import Response

        return Response(exc.as_body(), status=exc.http_status)
    return exception_handler(exc, context)
