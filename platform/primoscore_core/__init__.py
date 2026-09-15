"""Extracted calculation core. No application startup, storage or authentication."""

from .score_engine import calculate_score, get_engine_version

__all__ = ["calculate_score", "get_engine_version"]
