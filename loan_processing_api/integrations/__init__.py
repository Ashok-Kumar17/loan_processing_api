"""External-system adapters.

Each integration is an interface plus a deterministic mock, selected in
settings.INTEGRATIONS. The lending code only talks to the interface, so a real
CAMS / KFintech / bureau client can be dropped in without touching business logic.
"""
