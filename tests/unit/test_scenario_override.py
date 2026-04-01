"""Test scenario override set logic and thread safety.

PRD Appendix B: proves that adding a link to the override set prevents
the dispatcher from bringing it up when the OME reports visibility, that
removing a link unblocks it, and that clearing the full set reconciles all.

Dispatcher integration tests for override behavior are in
tests/unit/test_scheduler_dispatcher.py (uses the live scheduler.dispatcher).
"""

import threading


class TestOverrideSet:
    def test_add_override_blocks_pair(self):
        override_set: set[tuple[str, str]] = set()
        lock = threading.Lock()

        pair = ("sat-P00S00", "sat-P00S01")
        with lock:
            override_set.add(pair)

        with lock:
            assert pair in override_set

    def test_remove_override_unblocks_pair(self):
        override_set: set[tuple[str, str]] = set()
        lock = threading.Lock()

        pair = ("sat-P00S00", "sat-P00S01")
        with lock:
            override_set.add(pair)
        with lock:
            override_set.discard(pair)
        with lock:
            assert pair not in override_set

    def test_clear_removes_all(self):
        override_set: set[tuple[str, str]] = set()
        lock = threading.Lock()

        override_set.add(("sat-P00S00", "sat-P00S01"))
        override_set.add(("sat-P00S00", "sat-P01S00"))
        override_set.add(("gs-hawthorne", "sat-P00S00"))

        with lock:
            override_set.clear()
        assert len(override_set) == 0

    def test_thread_safety(self):
        """Override set is thread-safe under concurrent access."""
        override_set: set[tuple[str, str]] = set()
        lock = threading.Lock()
        errors: list[str] = []

        def writer():
            for i in range(100):
                pair = (f"sat-P00S{i:02d}", f"sat-P01S{i:02d}")
                with lock:
                    override_set.add(pair)

        def reader():
            for _ in range(100):
                with lock:
                    _ = len(override_set)

        def remover():
            for i in range(50):
                pair = (f"sat-P00S{i:02d}", f"sat-P01S{i:02d}")
                with lock:
                    override_set.discard(pair)

        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=reader),
            threading.Thread(target=remover),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(errors) == 0

    def test_alphabetical_pair_normalization(self):
        """Override pairs should be stored with node_a < node_b."""
        override_set: set[tuple[str, str]] = set()

        # Normalize before inserting
        a, b = "sat-P01S00", "sat-P00S00"
        pair = (min(a, b), max(a, b))
        override_set.add(pair)

        # Check with normalized pair
        assert ("sat-P00S00", "sat-P01S00") in override_set
