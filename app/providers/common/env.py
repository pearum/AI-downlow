"""Shared constants for provider adapters."""

from __future__ import annotations

import os

# Provider registry keys.
PROVIDER_IDS = ("youtube", "tiktok", "facebook")

# Read optional env vars without hard-coding secrets in source.
API_KEYS = {
    provider: os.environ.get(f"{provider.upper()}_API_KEY", "")
    for provider in PROVIDER_IDS
}
