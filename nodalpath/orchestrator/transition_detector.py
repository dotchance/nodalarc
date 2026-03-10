"""Transition detector — pure set diff on canonical link pairs."""

from __future__ import annotations


def detect_transition(
    prev: frozenset[tuple[str, str]],
    curr: frozenset[tuple[str, str]],
) -> tuple[frozenset[tuple[str, str]], frozenset[tuple[str, str]]]:
    """Compute added and removed links between two link sets.

    Returns (added, removed) as frozensets of canonical (node_a, node_b) pairs.
    """
    added = curr - prev
    removed = prev - curr
    return added, removed


def has_transition(
    prev: frozenset[tuple[str, str]],
    curr: frozenset[tuple[str, str]],
) -> bool:
    """Return True if the link sets differ."""
    return prev != curr
