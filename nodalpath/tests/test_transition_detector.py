"""Tests for nodalpath.orchestrator.transition_detector."""

from __future__ import annotations

from nodalpath.orchestrator.transition_detector import detect_transition, has_transition


class TestDetectTransition:
    """Tests for detect_transition()."""

    def test_identical_sets_no_change(self) -> None:
        """Identical sets produce empty added and removed."""
        links = frozenset({("a", "b"), ("c", "d")})
        added, removed = detect_transition(links, links)
        assert added == frozenset()
        assert removed == frozenset()

    def test_added_links(self) -> None:
        """New links appear in the added set."""
        prev = frozenset({("a", "b")})
        curr = frozenset({("a", "b"), ("c", "d")})
        added, removed = detect_transition(prev, curr)
        assert added == frozenset({("c", "d")})
        assert removed == frozenset()

    def test_removed_links(self) -> None:
        """Removed links appear in the removed set."""
        prev = frozenset({("a", "b"), ("c", "d")})
        curr = frozenset({("a", "b")})
        added, removed = detect_transition(prev, curr)
        assert added == frozenset()
        assert removed == frozenset({("c", "d")})

    def test_added_and_removed(self) -> None:
        """Simultaneous add and remove are both detected."""
        prev = frozenset({("a", "b"), ("c", "d")})
        curr = frozenset({("a", "b"), ("e", "f")})
        added, removed = detect_transition(prev, curr)
        assert added == frozenset({("e", "f")})
        assert removed == frozenset({("c", "d")})

    def test_empty_to_populated(self) -> None:
        """Transition from empty to populated set."""
        prev: frozenset[tuple[str, str]] = frozenset()
        curr = frozenset({("a", "b"), ("c", "d")})
        added, removed = detect_transition(prev, curr)
        assert added == curr
        assert removed == frozenset()

    def test_populated_to_empty(self) -> None:
        """Transition from populated to empty set."""
        prev = frozenset({("a", "b")})
        curr: frozenset[tuple[str, str]] = frozenset()
        added, removed = detect_transition(prev, curr)
        assert added == frozenset()
        assert removed == prev


class TestHasTransition:
    """Tests for has_transition()."""

    def test_identical_returns_false(self) -> None:
        links = frozenset({("a", "b")})
        assert has_transition(links, links) is False

    def test_different_returns_true(self) -> None:
        prev = frozenset({("a", "b")})
        curr = frozenset({("a", "b"), ("c", "d")})
        assert has_transition(prev, curr) is True

    def test_both_empty_returns_false(self) -> None:
        empty: frozenset[tuple[str, str]] = frozenset()
        assert has_transition(empty, empty) is False
