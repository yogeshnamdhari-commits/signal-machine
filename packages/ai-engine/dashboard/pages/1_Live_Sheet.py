"""Canonical Live Sheet page.

This page intentionally delegates to the same read-only dashboard contract as the
root dashboard. It contains no independent signal-generation logic.
"""
from __future__ import annotations

from dashboard.app import *  # noqa: F401,F403
