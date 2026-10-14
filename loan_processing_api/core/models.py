from django.conf import settings
from django.db import models


class IdempotencyKey(models.Model):
    """Stores the first response for a client-supplied Idempotency-Key.

    The (user, key) unique constraint is what makes concurrent retries safe: the
    second request blocks on the unique index until the first commits, then
    replays the stored response instead of moving money twice.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    key = models.CharField(max_length=128)
    scope = models.CharField(max_length=64)
    fingerprint = models.CharField(max_length=64)
    response_code = models.PositiveSmallIntegerField(null=True)
    response_body = models.JSONField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "key"], name="uniq_idempotency_key_per_user")]

    def __str__(self):
        return f"{self.scope}:{self.key}"


class AuditEvent(models.Model):
    """Append-only trail of every state change and money movement."""

    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    entity = models.CharField(max_length=40)
    entity_id = models.BigIntegerField()
    action = models.CharField(max_length=40)
    data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        indexes = [models.Index(fields=["entity", "entity_id"])]
        ordering = ["id"]

    def __str__(self):
        return f"{self.entity}#{self.entity_id} {self.action}"


def audit(actor, entity: str, entity_id: int, action: str, **data) -> AuditEvent:
    return AuditEvent.objects.create(
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        entity=entity,
        entity_id=entity_id,
        action=action,
        data={k: str(v) for k, v in data.items()},
    )
