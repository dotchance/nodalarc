"""Tests for the Nodal Unified Logging Library.

Covers: record enrichment, all three formatters, NatsHandler queueing,
code derivation (explicit + fallback), cardinality guard, session scoping,
idempotency, shutdown flushing, and multi-tenant field propagation.
"""

import contextlib
import io
import json
import logging
import sys

import pytest
from nodal.logging import (
    configure,
    set_session,
    set_tenant,
)
from nodal.logging._filter import NodalFilter, _pascal_to_upper_snake
from nodal.logging._formatter import HumanFormatter, JsonFormatter, OpsEventFormatter
from nodal.logging._nats_handler import NatsHandler


@pytest.fixture(autouse=True)
def _clean_root_logger():
    """Save and restore root logger state between tests."""
    root = logging.getLogger()
    old_handlers = root.handlers[:]
    old_filters = root.filters[:]
    old_level = root.level
    yield
    root.handlers = old_handlers
    root.filters = old_filters
    root.level = old_level


def _make_record(
    name="test.module",
    level=logging.WARNING,
    msg="test message",
    args=(),
    **extra,
):
    record = logging.LogRecord(name, level, "", 0, msg, args, None)
    for k, v in extra.items():
        setattr(record, k, v)
    return record


class TestNodalFilter:
    """NodalFilter enriches every LogRecord with nodal_* fields."""

    def test_enriches_record(self):
        filt = NodalFilter("nodal.arc.ome", session_id="demo-36")
        record = _make_record()
        filt.filter(record)

        assert hasattr(record, "nodal_ts")
        assert record.nodal_service == "nodal.arc.ome"
        assert record.nodal_source == "ome"
        assert record.nodal_session == "demo-36"
        assert record.nodal_level == "warning"
        assert isinstance(record.nodal_host, str)
        assert len(record.nodal_host) > 0

    def test_idempotent(self):
        filt = NodalFilter("nodal.arc.ome")
        record = _make_record()
        filt.filter(record)
        ts_first = record.nodal_ts

        filt.filter(record)
        assert record.nodal_ts == ts_first

    def test_level_normalization(self):
        filt = NodalFilter("nodal.arc.ome")

        for level, expected in [
            (logging.DEBUG, "debug"),
            (logging.INFO, "info"),
            (logging.WARNING, "warning"),
            (logging.ERROR, "error"),
            (logging.CRITICAL, "critical"),
        ]:
            record = _make_record(level=level)
            filt.filter(record)
            assert record.nodal_level == expected

    def test_source_derivation(self):
        assert NodalFilter._derive_source("nodal.arc.ome") == "ome"
        assert NodalFilter._derive_source("nodal.path.engine") == "engine"
        assert NodalFilter._derive_source("standalone") == "standalone"

    def test_session_snapshot_at_filter_time(self):
        filt = NodalFilter("nodal.arc.vs_api", session_id="session-A")

        record_a = _make_record()
        filt.filter(record_a)
        assert record_a.nodal_session == "session-A"

        filt.session_id = "session-B"

        record_b = _make_record()
        filt.filter(record_b)
        assert record_b.nodal_session == "session-B"
        assert record_a.nodal_session == "session-A"

    def test_tenant_propagated(self):
        filt = NodalFilter("nodal.arc.ome", tenant_id="acme", session_id="demo")
        record = _make_record()
        filt.filter(record)
        assert record.nodal_tenant == "acme"

    def test_details_from_extra(self):
        filt = NodalFilter("nodal.arc.ome")
        record = _make_record(details={"key": "val", "count": 42})
        filt.filter(record)
        assert record.nodal_details == {"key": "val", "count": 42}

    def test_details_none_by_default(self):
        filt = NodalFilter("nodal.arc.ome")
        record = _make_record()
        filt.filter(record)
        assert record.nodal_details is None

    def test_always_returns_true(self):
        filt = NodalFilter("nodal.arc.ome")
        record = _make_record()
        assert filt.filter(record) is True


class TestCodeDerivation:
    """Code derivation: explicit > fallback, with cardinality guard."""

    def test_explicit_code_overrides_derivation(self):
        filt = NodalFilter("nodal.arc.scheduler")
        record = _make_record(
            name="scheduler.dispatcher", msg="BatchLinkUp: 3 upped", code="BATCH_UP"
        )
        filt.filter(record)
        assert record.nodal_code == "BATCH_UP"

    def test_pascal_case_prefix_extracted(self):
        filt = NodalFilter("nodal.arc.scheduler")
        record = _make_record(name="scheduler.dispatcher", msg="BatchLinkUp: 3 upped")
        filt.filter(record)
        assert record.nodal_code == "DISPATCHER_BATCH_LINK_UP"

    def test_no_prefix_uses_logger_suffix(self):
        filt = NodalFilter("nodal.arc.scheduler")
        record = _make_record(name="scheduler.dispatcher", msg="Failed to connect")
        filt.filter(record)
        assert record.nodal_code == "DISPATCHER"

    def test_cardinality_guard_rejects_dynamic_ids(self):
        filt = NodalFilter("nodal.arc.scheduler")
        for msg in [
            "sat-P00S00: link degraded",
            "gs-london: unreachable",
            "node-04: timeout",
            "pod-abc123: killed",
        ]:
            record = _make_record(name="scheduler.dispatcher", msg=msg)
            filt.filter(record)
            assert record.nodal_code == "DISPATCHER", f"Dynamic ID leaked into code for: {msg}"

    def test_cardinality_guard_allows_static_labels(self):
        filt = NodalFilter("nodal.arc.scheduler")
        for msg, expected in [
            ("Satellite: battery low", "DISPATCHER_SATELLITE"),
            ("WiringComplete: 43 nodes", "DISPATCHER_WIRING_COMPLETE"),
            ("HTTPError: timeout", "DISPATCHER_HTTP_ERROR"),
            ("ISLCount: 500", "DISPATCHER_ISL_COUNT"),
        ]:
            record = _make_record(name="scheduler.dispatcher", msg=msg)
            filt.filter(record)
            assert record.nodal_code == expected, f"Wrong code for: {msg}"

    def test_consecutive_caps_handled(self):
        assert _pascal_to_upper_snake("HTTPError") == "HTTP_ERROR"
        assert _pascal_to_upper_snake("ISLCount") == "ISL_COUNT"
        assert _pascal_to_upper_snake("BatchLinkUp") == "BATCH_LINK_UP"
        assert _pascal_to_upper_snake("Simple") == "SIMPLE"


class TestHumanFormatter:
    """HumanFormatter produces readable terminal output."""

    def test_format_structure(self):
        filt = NodalFilter("nodal.arc.ome", session_id="demo")
        fmt = HumanFormatter()
        record = _make_record(name="ome.main", msg="Starting up")
        filt.filter(record)
        output = fmt.format(record)

        assert "Z" in output
        assert " INFO" in output or " WARN" in output
        assert "ome.main" in output
        assert "—" in output
        assert "Starting up" in output

    def test_warn_not_warning(self):
        fmt = HumanFormatter()
        record = _make_record(level=logging.WARNING, msg="test")
        output = fmt.format(record)
        assert " WARN " in output
        assert "WARNING" not in output

    def test_exception_included(self):
        fmt = HumanFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            record = _make_record(msg="failed")
            record.exc_info = sys.exc_info()
        output = fmt.format(record)
        assert "ValueError: boom" in output
        assert "Traceback" in output


class TestJsonFormatter:
    """JsonFormatter produces valid JSONL with all fields."""

    def test_valid_json(self):
        filt = NodalFilter("nodal.arc.scheduler", session_id="demo", tenant_id="acme")
        fmt = JsonFormatter()
        record = _make_record(name="scheduler.dispatcher", msg="test msg")
        filt.filter(record)
        output = fmt.format(record)
        obj = json.loads(output)

        assert obj["ts"] != ""
        assert obj["level"] == "warning"
        assert obj["service"] == "nodal.arc.scheduler"
        assert obj["logger"] == "scheduler.dispatcher"
        assert obj["session"] == "demo"
        assert obj["tenant"] == "acme"
        assert obj["host"] != ""
        assert obj["code"] != ""
        assert obj["msg"] == "test msg"

    def test_details_not_double_serialized(self):
        filt = NodalFilter("nodal.arc.scheduler")
        fmt = JsonFormatter()
        record = _make_record(msg="test", details={"nested": {"key": "val"}})
        filt.filter(record)
        output = fmt.format(record)
        obj = json.loads(output)

        assert isinstance(obj["details"], dict)
        assert isinstance(obj["details"]["nested"], dict)
        assert obj["details"]["nested"]["key"] == "val"

    def test_exception_in_exc_field(self):
        filt = NodalFilter("nodal.arc.ome")
        fmt = JsonFormatter()
        try:
            raise RuntimeError("crash")
        except RuntimeError:
            record = _make_record(msg="failed")
            record.exc_info = sys.exc_info()
        filt.filter(record)
        output = fmt.format(record)
        obj = json.loads(output)

        assert "exc" in obj
        assert "RuntimeError: crash" in obj["exc"]


class TestOpsEventFormatter:
    """OpsEventFormatter produces OpsEvent-compatible JSON."""

    def test_matches_ops_event_schema(self):
        filt = NodalFilter("nodal.arc.node_agent", session_id="demo-36")
        fmt = OpsEventFormatter()
        record = _make_record(name="node_agent.handlers", msg="WiringComplete: 43 nodes wired")
        filt.filter(record)
        output = fmt.format(record)
        obj = json.loads(output)

        assert "timestamp" in obj
        assert obj["session_id"] == "demo-36"
        assert obj["source"] == "node_agent"
        assert obj["hostname"] != ""
        assert obj["level"] == "warning"
        assert obj["code"] == "HANDLERS_WIRING_COMPLETE"
        assert obj["message"] == "WiringComplete: 43 nodes wired"
        assert "details" in obj

    def test_tenant_included_when_set(self):
        filt = NodalFilter("nodal.arc.ome", tenant_id="acme")
        fmt = OpsEventFormatter()
        record = _make_record(msg="test")
        filt.filter(record)
        obj = json.loads(fmt.format(record))
        assert obj["tenant_id"] == "acme"

    def test_tenant_omitted_when_empty(self):
        filt = NodalFilter("nodal.arc.ome")
        fmt = OpsEventFormatter()
        record = _make_record(msg="test")
        filt.filter(record)
        obj = json.loads(fmt.format(record))
        assert "tenant_id" not in obj

    def test_exception_traceback_in_details(self):
        filt = NodalFilter("nodal.arc.ome")
        fmt = OpsEventFormatter()
        try:
            raise ValueError("connection refused on port 5432")
        except ValueError:
            record = _make_record(msg="DB connect failed")
            record.exc_info = sys.exc_info()
        filt.filter(record)
        obj = json.loads(fmt.format(record))

        assert obj["message"] == "DB connect failed"
        assert obj["details"] is not None
        assert "traceback" in obj["details"]
        assert "ValueError: connection refused on port 5432" in obj["details"]["traceback"]
        assert "Traceback" in obj["details"]["traceback"]

    def test_exception_merged_with_caller_details(self):
        filt = NodalFilter("nodal.arc.ome")
        fmt = OpsEventFormatter()
        try:
            raise RuntimeError("timeout")
        except RuntimeError:
            record = _make_record(msg="failed", details={"attempt": 3})
            record.exc_info = sys.exc_info()
        filt.filter(record)
        obj = json.loads(fmt.format(record))

        assert obj["details"]["attempt"] == 3
        assert "RuntimeError: timeout" in obj["details"]["traceback"]

    def test_no_exception_leaves_details_unchanged(self):
        filt = NodalFilter("nodal.arc.ome")
        fmt = OpsEventFormatter()
        record = _make_record(msg="ok", details={"key": "val"})
        filt.filter(record)
        obj = json.loads(fmt.format(record))

        assert obj["details"] == {"key": "val"}
        assert "traceback" not in obj["details"]


class TestNatsHandler:
    """NatsHandler queues records and builds NATS subjects."""

    def test_queues_record_at_level(self):
        handler = NatsHandler("nodal.arc.ome", level=logging.WARNING)
        filt = NodalFilter("nodal.arc.ome", session_id="demo")
        handler.addFilter(filt)

        record = _make_record(level=logging.WARNING, msg="important")
        handler.handle(record)
        assert len(handler._deque) == 1

    def test_ignores_record_below_level(self):
        handler = NatsHandler("nodal.arc.ome", level=logging.WARNING)
        filt = NodalFilter("nodal.arc.ome")
        handler.addFilter(filt)

        root = logging.getLogger()
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)

        log = logging.getLogger("ome.test_level")
        log.info("ignored")
        assert len(handler._deque) == 0

    def test_deque_overflow_drops_oldest(self):
        handler = NatsHandler("nodal.arc.ome", level=logging.WARNING)
        filt = NodalFilter("nodal.arc.ome")
        handler.addFilter(filt)

        for i in range(600):
            record = _make_record(msg=f"msg-{i}")
            handler.handle(record)

        assert len(handler._deque) == 500
        _subject, payload = handler._deque[0]
        obj = json.loads(payload)
        assert obj["message"] == "msg-100"

    def test_subject_infra_scope(self):
        handler = NatsHandler("nodal.arc.ome")
        record = _make_record(msg="test")
        record.nodal_tenant = ""
        record.nodal_session = ""
        record.nodal_source = "ome"
        record.nodal_code = "STARTUP"

        subject = handler._build_subject(record)
        assert subject == "nodalarc.ops._infra.ome.startup"

    def test_subject_session_scope(self):
        handler = NatsHandler("nodal.arc.ome")
        record = _make_record()
        record.nodal_tenant = ""
        record.nodal_session = "demo-36"
        record.nodal_source = "ome"
        record.nodal_code = "RECOVERY"

        subject = handler._build_subject(record)
        assert subject == "nodalarc.ops.demo-36.ome.recovery"

    def test_subject_tenant_session_scope(self):
        handler = NatsHandler("nodal.arc.ome")
        record = _make_record()
        record.nodal_tenant = "acme"
        record.nodal_session = "demo-36"
        record.nodal_source = "ome"
        record.nodal_code = "RECOVERY"

        subject = handler._build_subject(record)
        assert subject == "nodalarc.ops.acme.demo-36.ome.recovery"

    def test_subject_tenant_only_scope(self):
        handler = NatsHandler("nodal.arc.operator")
        record = _make_record()
        record.nodal_tenant = "acme"
        record.nodal_session = ""
        record.nodal_source = "operator"
        record.nodal_code = "DEPLOY"

        subject = handler._build_subject(record)
        assert subject == "nodalarc.ops.acme._tenant.operator.deploy"

    def test_subject_no_code(self):
        handler = NatsHandler("nodal.arc.ome")
        record = _make_record()
        record.nodal_tenant = ""
        record.nodal_session = "demo-36"
        record.nodal_source = "ome"
        record.nodal_code = ""

        subject = handler._build_subject(record)
        assert subject == "nodalarc.ops.demo-36.ome"


class TestConfigure:
    """configure() sets up root logger with correct handlers."""

    def test_sets_up_two_handlers(self):
        configure("nodal.arc.ome")
        root = logging.getLogger()
        handler_types = [type(h).__name__ for h in root.handlers]
        assert "StreamHandler" in handler_types
        assert "NatsHandler" in handler_types
        assert len(root.handlers) == 2

    def test_idempotent(self):
        configure("nodal.arc.ome")
        configure("nodal.arc.ome")
        root = logging.getLogger()
        assert len(root.handlers) == 2

    def test_child_logger_enriched(self):
        configure("nodal.arc.scheduler", session_id="demo")
        log = logging.getLogger("scheduler.dispatcher")

        stream_handler = None
        for h in logging.getLogger().handlers:
            if isinstance(h, logging.StreamHandler) and not isinstance(h, NatsHandler):
                stream_handler = h
                break

        assert stream_handler is not None
        buf = io.StringIO()
        stream_handler.stream = buf

        log.warning("test message")
        output = buf.getvalue()
        assert "WARN" in output
        assert "scheduler.dispatcher" in output
        assert "test message" in output

    def test_third_party_silenced(self):
        configure("nodal.arc.ome")
        for name in ("nats", "kubernetes", "asyncio", "urllib3", "asyncssh"):
            assert logging.getLogger(name).level >= logging.WARNING

    def test_set_session_updates_future_records(self):
        configure("nodal.arc.vs_api", session_id="session-A")
        nats_handler = None
        for h in logging.getLogger().handlers:
            if isinstance(h, NatsHandler):
                nats_handler = h
                break

        log = logging.getLogger("vs_api.main")
        log.warning("msg from A")
        assert len(nats_handler._deque) == 1
        _subj, payload_a = nats_handler._deque[0]
        assert json.loads(payload_a)["session_id"] == "session-A"

        set_session("session-B")
        log.warning("msg from B")
        assert len(nats_handler._deque) == 2
        _subj, payload_b = nats_handler._deque[1]
        assert json.loads(payload_b)["session_id"] == "session-B"

    def test_session_captured_at_log_time(self):
        configure("nodal.arc.vs_api", session_id="session-A")
        nats_handler = None
        for h in logging.getLogger().handlers:
            if isinstance(h, NatsHandler):
                nats_handler = h
                break

        log = logging.getLogger("vs_api.main")

        for i in range(5):
            log.warning("msg-%d from A", i)

        set_session("session-B")

        for _subj, payload in nats_handler._deque:
            obj = json.loads(payload)
            assert obj["session_id"] == "session-A", (
                "Record queued during session-A must retain session-A ID"
            )

    def test_json_format_selected(self):
        configure("nodal.arc.ome", stdout_format="json")
        stream_handler = None
        for h in logging.getLogger().handlers:
            if isinstance(h, logging.StreamHandler) and not isinstance(h, NatsHandler):
                stream_handler = h
                break
        assert isinstance(stream_handler.formatter, JsonFormatter)

    def test_nats_level_respected(self):
        configure("nodal.arc.ome", nats_level=logging.ERROR)
        nats_handler = None
        for h in logging.getLogger().handlers:
            if isinstance(h, NatsHandler):
                nats_handler = h
                break

        log = logging.getLogger("ome.main")
        log.warning("should not queue")
        assert len(nats_handler._deque) == 0

        log.error("should queue")
        assert len(nats_handler._deque) == 1

    def test_set_tenant(self):
        configure("nodal.arc.ome", tenant_id="acme")
        set_tenant("newcorp")

        nats_handler = None
        for h in logging.getLogger().handlers:
            if isinstance(h, NatsHandler):
                nats_handler = h
                break

        log = logging.getLogger("ome.main")
        log.warning("test")
        _subj, payload = nats_handler._deque[0]
        obj = json.loads(payload)
        assert obj.get("tenant_id") == "newcorp"


class TestShutdownFlushing:
    """atexit handler dumps unflushed records."""

    def test_lame_duck_dumps_to_stderr(self, capsys):
        configure("nodal.arc.ome", session_id="demo", nats_level=logging.WARNING)

        nats_handler = None
        for h in logging.getLogger().handlers:
            if isinstance(h, NatsHandler):
                nats_handler = h
                break

        log = logging.getLogger("ome.main")
        log.warning("important failure message")
        assert len(nats_handler._deque) == 1

        nats_handler.flush_sync(timeout=1.0)

        captured = capsys.readouterr()
        assert "important failure message" in captured.err
        assert "unflushed" in captured.err
        assert len(nats_handler._deque) == 0


class TestDrainLoop:
    """Async drain task publishes queued records to NATS."""

    def test_connect_drains_to_nats(self):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        handler = NatsHandler("nodal.arc.scheduler", level=logging.WARNING)
        filt = NodalFilter("nodal.arc.scheduler", session_id="demo-36")
        handler.addFilter(filt)

        for i in range(3):
            record = _make_record(name="scheduler.dispatcher", msg=f"msg-{i}")
            handler.handle(record)
        assert len(handler._deque) == 3

        nc = MagicMock()
        js_mock = MagicMock()
        js_mock.publish = AsyncMock()
        nc.jetstream.return_value = js_mock

        async def run():
            await handler.connect(nc)
            await asyncio.sleep(0.05)
            handler._drain_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await handler._drain_task

        asyncio.run(run())

        assert js_mock.publish.call_count == 3
        assert len(handler._deque) == 0

        subjects = [call.args[0] for call in js_mock.publish.call_args_list]
        for s in subjects:
            assert s.startswith("nodalarc.ops.demo-36.scheduler.")

    def test_drain_counts_dropped_records(self, capsys):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        handler = NatsHandler("nodal.arc.ome", level=logging.WARNING)
        filt = NodalFilter("nodal.arc.ome", session_id="demo")
        handler.addFilter(filt)

        for i in range(5):
            record = _make_record(msg=f"fail-{i}")
            handler.handle(record)

        nc = MagicMock()
        js_mock = MagicMock()
        js_mock.publish = AsyncMock(side_effect=Exception("NATS down"))
        nc.jetstream.return_value = js_mock

        async def run():
            await handler.connect(nc)
            await asyncio.sleep(0.05)
            handler._drain_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await handler._drain_task

        asyncio.run(run())

        captured = capsys.readouterr()
        assert "record(s) dropped" in captured.err
        total_accounted = handler._dropped_since_last_report
        assert "1 record(s) dropped" in captured.err
        assert total_accounted == 4
        assert len(handler._deque) == 0

    def test_drain_task_cancels_cleanly(self):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        handler = NatsHandler("nodal.arc.ome", level=logging.WARNING)

        nc = MagicMock()
        js_mock = MagicMock()
        js_mock.publish = AsyncMock()
        nc.jetstream.return_value = js_mock

        async def run():
            await handler.connect(nc)
            assert not handler._drain_task.done()
            handler._drain_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await handler._drain_task
            assert handler._drain_task.done()

        asyncio.run(run())

    def test_large_prebuffer_drains_completely(self):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        handler = NatsHandler("nodal.arc.ome", level=logging.WARNING)
        filt = NodalFilter("nodal.arc.ome", session_id="test")
        handler.addFilter(filt)

        for i in range(300):
            record = _make_record(msg=f"msg-{i}")
            handler.handle(record)
        assert len(handler._deque) == 300

        nc = MagicMock()
        js_mock = MagicMock()
        js_mock.publish = AsyncMock()
        nc.jetstream.return_value = js_mock

        async def run():
            await handler.connect(nc)
            await asyncio.sleep(0.1)
            handler._drain_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await handler._drain_task

        asyncio.run(run())

        assert js_mock.publish.call_count == 300
        assert len(handler._deque) == 0


# ---------------------------------------------------------------------------
# On-demand debug: set_nats_level + subject routing
# ---------------------------------------------------------------------------


class TestSetNatsLevel:
    """Tests for NatsHandler.set_nats_level() — the package logger approach."""

    def setup_method(self):
        self.root = logging.getLogger()
        self.root.setLevel(logging.INFO)
        for h in self.root.handlers[:]:
            self.root.removeHandler(h)

    def teardown_method(self):
        logging.getLogger("scheduler").setLevel(logging.NOTSET)
        logging.getLogger("ome").setLevel(logging.NOTSET)
        logging.getLogger("node_agent").setLevel(logging.NOTSET)

    def test_enable_sets_handler_and_package_logger_to_debug(self):
        handler = NatsHandler("nodal.arc.scheduler", level=logging.INFO)
        handler.set_nats_level(logging.DEBUG)
        assert handler.level == logging.DEBUG
        assert logging.getLogger("scheduler").level == logging.DEBUG

    def test_disable_resets_handler_and_package_logger(self):
        handler = NatsHandler("nodal.arc.scheduler", level=logging.INFO)
        handler.set_nats_level(logging.DEBUG)
        handler.set_nats_level(logging.INFO)
        assert handler.level == logging.INFO
        assert logging.getLogger("scheduler").level == logging.NOTSET

    def test_root_logger_never_changes(self):
        handler = NatsHandler("nodal.arc.scheduler", level=logging.INFO)
        assert self.root.level == logging.INFO
        handler.set_nats_level(logging.DEBUG)
        assert self.root.level == logging.INFO
        handler.set_nats_level(logging.INFO)
        assert self.root.level == logging.INFO

    def test_debug_records_reach_handler_when_enabled(self):
        handler = NatsHandler("nodal.arc.scheduler", level=logging.INFO)
        self.root.addHandler(handler)
        handler.set_nats_level(logging.DEBUG)

        log = logging.getLogger("scheduler.dispatcher")
        log.debug("test debug message")

        assert len(handler._deque) == 1
        subject, _ = handler._deque[0]
        assert "nodalarc.debug." in subject

    def test_debug_records_blocked_when_disabled(self):
        handler = NatsHandler("nodal.arc.scheduler", level=logging.INFO)
        self.root.addHandler(handler)

        log = logging.getLogger("scheduler.dispatcher")
        log.debug("test debug message")

        assert len(handler._deque) == 0

    def test_other_packages_unaffected_when_debug_enabled(self):
        handler = NatsHandler("nodal.arc.scheduler", level=logging.INFO)
        self.root.addHandler(handler)
        handler.set_nats_level(logging.DEBUG)

        ome_log = logging.getLogger("ome.main")
        ome_log.debug("ome debug should not appear")

        # Only the scheduler debug should be in the deque, not ome
        subjects = [s for s, _ in handler._deque]
        for s in subjects:
            assert "ome" not in s

    def test_info_records_use_ops_prefix(self):
        handler = NatsHandler("nodal.arc.scheduler", level=logging.INFO)
        nf = NodalFilter("nodal.arc.scheduler")
        handler.addFilter(nf)
        self.root.addHandler(handler)

        log = logging.getLogger("scheduler.dispatcher")
        log.info("test info message")

        assert len(handler._deque) == 1
        subject, _ = handler._deque[0]
        assert subject.startswith("nodalarc.ops.")

    def test_debug_records_use_debug_prefix(self):
        handler = NatsHandler("nodal.arc.scheduler", level=logging.INFO)
        nf = NodalFilter("nodal.arc.scheduler")
        handler.addFilter(nf)
        self.root.addHandler(handler)
        handler.set_nats_level(logging.DEBUG)

        log = logging.getLogger("scheduler.dispatcher")
        log.debug("test debug message")

        assert len(handler._deque) == 1
        subject, _ = handler._deque[0]
        assert subject.startswith("nodalarc.debug.")

    def test_multiple_packages_can_be_debug_simultaneously(self):
        handler_sched = NatsHandler("nodal.arc.scheduler", level=logging.INFO)
        handler_sched.set_nats_level(logging.DEBUG)
        handler_ome = NatsHandler("nodal.arc.ome", level=logging.INFO)
        handler_ome.set_nats_level(logging.DEBUG)

        assert logging.getLogger("scheduler").level == logging.DEBUG
        assert logging.getLogger("ome").level == logging.DEBUG
        assert logging.getLogger("node_agent").level == logging.NOTSET

    def test_stdout_handler_rejects_debug_regardless(self):
        stdout_handler = logging.StreamHandler(io.StringIO())
        stdout_handler.setLevel(logging.INFO)
        self.root.addHandler(stdout_handler)

        handler = NatsHandler("nodal.arc.scheduler", level=logging.INFO)
        self.root.addHandler(handler)
        handler.set_nats_level(logging.DEBUG)

        log = logging.getLogger("scheduler.dispatcher")
        log.debug("should not appear on stdout")

        output = stdout_handler.stream.getvalue()
        assert output == ""

    def test_deep_nested_loggers_inherit_debug(self):
        handler = NatsHandler("nodal.arc.scheduler", level=logging.INFO)
        self.root.addHandler(handler)
        handler.set_nats_level(logging.DEBUG)

        deep_log = logging.getLogger("scheduler.dispatcher.reconcile.internal")
        deep_log.debug("deep nested debug")

        assert len(handler._deque) == 1
