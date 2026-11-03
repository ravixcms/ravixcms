"""Removes what is no longer needed. Called by ``ravix_cron`` (every 5 minutes on shared hosting); safe to run any time."""
from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from .models import AuthorizationCode, McpConnection, McpToken, OAuthClient


def cleanup(now=None) -> dict:
    now = now or timezone.now()
    counts = {}
    counts["tokens"] = McpToken.objects.filter(expires_at__lt=now - timedelta(days=1)).delete()[0]
    counts["codes"] = AuthorizationCode.objects.filter(expires_at__lt=now - timedelta(hours=1)).delete()[0]
    # An idle connection stops working by itself; the row is kept a while so the person can see that it ended, then removed.
    idle_before = now - timedelta(days=90)
    for conn in McpConnection.objects.filter(revoked_at__isnull=True, last_used_at__lt=idle_before) | McpConnection.objects.filter(revoked_at__isnull=True, last_used_at__isnull=True, created_at__lt=idle_before):
        conn.revoke("idle")
    counts["connections"] = McpConnection.objects.filter(revoked_at__lt=now - timedelta(days=30)).delete()[0]
    counts["clients"] = OAuthClient.objects.filter(kind=OAuthClient.Kind.DCR, created_at__lt=now - timedelta(days=7), connections__isnull=True).delete()[0]
    return counts
