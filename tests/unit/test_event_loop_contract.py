# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Event-loop blocking contract — taint-based.

The defect class: BLOCKING OR UNBOUNDED-COST WORK ON A LATENCY-CRITICAL
ASYNCIO LOOP. A frozen loop freezes every websocket sender and NATS
handler sharing it; the cost is invisible at birth and grows with session
size or dependency latency.

Why v1 of this gate was insufficient (both proven live 2026-06-11):
1. It matched only bare denylisted names called DIRECTLY inside async
   bodies. The original incident call was ``_session_manager.rescan()`` —
   a method spelling the exact-name matcher could not see.
2. One frame of sync-wrapper indirection hid everything: after the rescan
   was removed, the same 2 s trust poll still ran a ~0.5 s full session
   resolution per iteration via ``_extract_ready_cr_session →
   _parse_session_yaml → resolve_session_with_assets`` (measured 0.494 s
   median in-pod for the 132-node flagship), and v1 passed.

v2 mechanics — whole-service taint analysis:
- Collect every function/method body in the scanned service trees.
- Seed taint at denylisted blocking calls (full-name, terminal-name, and
  attribute-suffix matching, so method spellings and aliases count).
- Propagate taint through sync call chains to a fixpoint, including class
  instantiation (``ClassName(...)`` taints via ``__init__``).
- Flag every async function whose body calls a blocking or tainted callee.
- Offload semantics are exact: ``asyncio.to_thread(f, *args)`` is safe
  (f is passed, not called); ``asyncio.to_thread(f())`` is a violation —
  f() executes inline on the loop and only its RESULT ships to the
  thread; ``asyncio.to_thread(lambda: f())`` is safe (the lambda body
  runs in the worker thread).
- ``# loop-blocking-ok: <reason>`` on the call line or the line above
  exempts the call; in sync bodies it also stops taint from seeding
  there. The reason must state why the work is bounded.

Accepted limits (documented, deliberate): taint keys on simple function
names, so a clean function sharing a name with a tainted one is flagged
conservatively (annotate it); taint does not follow callables stored in
variables or functools.partial objects; lib/ bodies are not collected —
lib entry points must appear in the denylists by name.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# Async services whose event loops serve latency-critical traffic.
SCAN_ROOTS = [
    REPO / "services" / "vs_api",
    REPO / "services" / "ome",
    REPO / "services" / "scheduler",
    REPO / "services" / "nodalarc_operator",
]

# Fully-dotted call spellings that block, matched exactly as written.
BLOCKING_FULL_NAMES = {
    "time.sleep",
    "_time.sleep",
    "sqlite3.connect",
    "subprocess.run",
    "subprocess.check_output",
}

# Terminal (rightmost) name segments that block regardless of receiver
# spelling — this catches method calls (obj.rescan()) and import aliases.
# Curated for lib/ entry points and calls whose terminal name is
# unambiguous in this codebase; extend as new blocking APIs arrive.
BLOCKING_TERMINAL_NAMES = {
    # full catalog/session resolution (CPU-bound, scales with session size)
    "resolve_session",
    "resolve_session_with_assets",
    "load_session_resolution_from_file",
    "scan_sessions",
    "rescan",
    # YAML parse of session-sized documents
    "safe_load",
    # sync clients and file I/O
    "urlopen",
    "load_incluster_config",
    "load_kube_config",
    "write_text_exclusive",
}

BLOCKING_ATTR_SUFFIXES = (
    # kubernetes sync client method families
    "get_namespaced_custom_object",
    "list_namespaced_custom_object",
    "patch_namespaced_custom_object",
    "patch_namespaced_custom_object_status",
    "create_namespaced_custom_object",
    "delete_namespaced_custom_object",
    "list_namespaced_pod",
    "read_namespaced_pod",
    "read_namespaced_secret",
    # file I/O on Path objects
    "write_text",
    "read_text",
    "write_bytes",
    "read_bytes",
)

ALLOW_ANNOTATION = "loop-blocking-ok:"

_OFFLOADER_SUFFIXES = ("to_thread", "run_in_executor")


def _call_name(node: ast.Call) -> str:
    func = node.func
    parts: list[str] = []
    while isinstance(func, ast.Attribute):
        parts.append(func.attr)
        func = func.value
    if isinstance(func, ast.Name):
        parts.append(func.id)
    return ".".join(reversed(parts))


def _terminal(name: str) -> str:
    return name.rsplit(".", 1)[-1]


def _is_blocking_name(name: str) -> bool:
    return (
        name in BLOCKING_FULL_NAMES
        or _terminal(name) in BLOCKING_TERMINAL_NAMES
        or any(name.endswith(suffix) for suffix in BLOCKING_ATTR_SUFFIXES)
    )


@dataclass
class _Fn:
    """One collected function/method body and the calls it directly owns."""

    name: str
    qualname: str
    file: str
    is_async: bool
    calls: list[ast.Call] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)


def _annotated(fn: _Fn, call: ast.Call) -> bool:
    """Marker on the call line, or anywhere in the contiguous comment
    block directly above it — annotations are encouraged to explain the
    bound across multiple comment lines."""
    idx = call.lineno - 1
    if idx < len(fn.lines) and ALLOW_ANNOTATION in fn.lines[idx]:
        return True
    j = idx - 1
    while j >= 0 and fn.lines[j].lstrip().startswith("#"):
        if ALLOW_ANNOTATION in fn.lines[j]:
            return True
        j -= 1
    return False


def _offload_exempt(call: ast.Call, parents: dict[ast.AST, ast.AST]) -> bool:
    """Exempt only calls whose enclosing lambda is handed to an offloader.

    A blocking call that is itself an ARGUMENT of to_thread/run_in_executor
    executes inline on the loop (only its result ships to the thread) and
    is NOT exempt — that is the classic false-offload mistake.
    """
    node: ast.AST = call
    while True:
        parent = parents.get(node)
        if parent is None:
            return False
        if isinstance(parent, ast.Lambda):
            grand = parents.get(parent)
            if isinstance(grand, ast.Call) and _call_name(grand).endswith(_OFFLOADER_SUFFIXES):
                return True
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return False
        node = parent


def _collect(
    sources: dict[str, str],
) -> tuple[list[_Fn], dict[str, list[_Fn]], dict[ast.AST, ast.AST]]:
    """Parse sources into owned-call function records and a name index.

    Class ``__init__`` bodies are additionally indexed under the class
    name so that ``ClassName(...)`` instantiation propagates taint.
    """
    fns: list[_Fn] = []
    parents: dict[ast.AST, ast.AST] = {}
    for file, source in sources.items():
        tree = ast.parse(source)
        lines = source.splitlines()
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent

        def_nodes = [
            n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        node_to_fn: dict[ast.AST, _Fn] = {}
        for d in def_nodes:
            names = [d.name]
            owner = parents.get(d)
            if d.name == "__init__" and isinstance(owner, ast.ClassDef):
                names = [owner.name]
            fn = _Fn(
                name=names[0],
                qualname=f"{owner.name}.{d.name}" if isinstance(owner, ast.ClassDef) else d.name,
                file=file,
                is_async=isinstance(d, ast.AsyncFunctionDef),
                lines=lines,
            )
            node_to_fn[d] = fn
            fns.append(fn)

        # Assign each call to its nearest enclosing def.
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            anc = parents.get(node)
            while anc is not None and not isinstance(anc, (ast.FunctionDef, ast.AsyncFunctionDef)):
                anc = parents.get(anc)
            if anc is not None and anc in node_to_fn:
                node_to_fn[anc].calls.append(node)

    by_name: dict[str, list[_Fn]] = {}
    for fn in fns:
        by_name.setdefault(fn.name, []).append(fn)
    return fns, by_name, parents


def _taint(
    fns: list[_Fn], parents: dict[ast.AST, ast.AST]
) -> tuple[dict[tuple[str, str], str], dict[str, str], dict[str, set[str]]]:
    """Fixpoint taint propagation across sync function bodies.

    Resolution is same-file-first: a call whose terminal name is defined in
    the caller's own file is judged against THAT definition only, so a
    clean local function is never tainted by an unrelated same-named
    function in another service. Calls with no local definition (method
    calls on foreign objects, cross-module helpers) fall back to a global
    name match — that fallback is what catches ``obj.rescan()``.

    Async functions are not taint carriers — calling one returns a
    coroutine without executing the body; the body is judged directly.
    """
    defs_in_file: dict[str, set[str]] = {}
    for fn in fns:
        defs_in_file.setdefault(fn.file, set()).add(fn.name)
    tainted: dict[tuple[str, str], str] = {}
    tainted_names: dict[str, str] = {}

    def _resolve(file: str, terminal: str) -> str | None:
        if terminal in defs_in_file.get(file, ()):
            return tainted.get((file, terminal))
        return tainted_names.get(terminal)

    sync_fns = [f for f in fns if not f.is_async]
    changed = True
    while changed:
        changed = False
        for fn in sync_fns:
            if (fn.file, fn.name) in tainted:
                continue
            for call in fn.calls:
                if _annotated(fn, call) or _offload_exempt(call, parents):
                    continue
                name = _call_name(call)
                if _is_blocking_name(name):
                    reason = f"{fn.qualname}() calls blocking '{name}'"
                else:
                    t = _terminal(name)
                    sub = _resolve(fn.file, t) if t != fn.name else None
                    if sub is None:
                        continue
                    reason = f"{fn.qualname}() calls '{name}' → {sub}"
                tainted[(fn.file, fn.name)] = reason
                tainted_names.setdefault(fn.name, reason)
                changed = True
                break
    return tainted, tainted_names, defs_in_file


def _violations(sources: dict[str, str]) -> list[str]:
    fns, _by_name, parents = _collect(sources)
    tainted, tainted_names, defs_in_file = _taint(fns, parents)

    def _resolve(file: str, terminal: str) -> str | None:
        if terminal in defs_in_file.get(file, ()):
            return tainted.get((file, terminal))
        return tainted_names.get(terminal)

    out: list[str] = []
    for fn in fns:
        if not fn.is_async:
            continue
        for call in fn.calls:
            name = _call_name(call)
            reason: str | None = None
            if _is_blocking_name(name):
                reason = f"directly calls blocking '{name}'"
            else:
                sub = _resolve(fn.file, _terminal(name))
                if sub is not None:
                    reason = f"calls '{name}' → {sub}"
            if reason is None:
                continue
            if _offload_exempt(call, parents) or _annotated(fn, call):
                continue
            out.append(f"{fn.file}:{call.lineno} async {fn.qualname}() {reason}")
    return out


def _service_sources() -> dict[str, str]:
    sources: dict[str, str] = {}
    for root in SCAN_ROOTS:
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in str(path):
                continue
            sources[str(path.relative_to(REPO))] = path.read_text()
    return sources


def test_no_blocking_calls_on_event_loops():
    all_violations = _violations(_service_sources())
    assert not all_violations, (
        "Blocking work on an event loop freezes every consumer sharing it "
        "(websocket senders, NATS handlers) — including work hidden one or "
        "more sync-wrapper frames down. Offload with asyncio.to_thread(fn, "
        "...) (passing the function, never calling it), or if the call is "
        "provably bounded, annotate the line with "
        "'# loop-blocking-ok: <reason>'.\n" + "\n".join(all_violations)
    )


# --- analyzer self-tests: pin the detector against the shapes that evaded v1 ---


def test_detects_direct_blocking_call():
    src = "async def h():\n    time.sleep(1)\n"
    assert len(_violations({"m.py": src})) == 1


def test_detects_method_spelling_of_denylisted_name():
    # Incident 1 shape: the v1 exact-name matcher missed obj.rescan().
    src = "async def h(mgr):\n    mgr.rescan()\n"
    assert len(_violations({"m.py": src})) == 1


def test_detects_blocking_behind_sync_wrappers():
    # Incident 2 shape: blocking work two sync frames below the async body.
    src = (
        "def parse(text):\n"
        "    return resolve_session_with_assets(text)\n"
        "def extract(cr):\n"
        "    return parse(cr)\n"
        "async def poll(cr):\n"
        "    return extract(cr)\n"
    )
    v = _violations({"m.py": src})
    assert len(v) == 1 and "extract" in v[0]


def test_detects_tainted_class_instantiation():
    src = (
        "class Ctx:\n"
        "    def __init__(self, p):\n"
        "        self.raw = p.read_text()\n"
        "async def activate(p):\n"
        "    return Ctx(p)\n"
    )
    v = _violations({"m.py": src})
    assert len(v) == 1 and "Ctx" in v[0]


def test_to_thread_passing_function_is_clean():
    src = (
        "def heavy(x):\n"
        "    return resolve_session_with_assets(x)\n"
        "async def h(x):\n"
        "    return await asyncio.to_thread(heavy, x)\n"
    )
    assert _violations({"m.py": src}) == []


def test_to_thread_calling_function_inline_is_flagged():
    # asyncio.to_thread(f()) runs f() ON the loop; only its result ships.
    src = (
        "def heavy(x):\n"
        "    return resolve_session_with_assets(x)\n"
        "async def h(x):\n"
        "    return await asyncio.to_thread(heavy(x))\n"
    )
    v = _violations({"m.py": src})
    assert len(v) == 1 and "heavy" in v[0]


def test_lambda_body_inside_offloader_is_clean():
    src = (
        "def heavy(x):\n"
        "    return resolve_session_with_assets(x)\n"
        "async def h(x):\n"
        "    return await asyncio.to_thread(lambda: heavy(x))\n"
    )
    assert _violations({"m.py": src}) == []


def test_annotation_exempts_with_reason():
    src = (
        "async def h(p):\n"
        "    # loop-blocking-ok: two-line config file, microseconds\n"
        "    return p.read_text()\n"
    )
    assert _violations({"m.py": src}) == []


def test_annotation_in_sync_body_stops_taint():
    src = (
        "def bounded(p):\n"
        "    return p.read_text()  # loop-blocking-ok: bounded marker file\n"
        "async def h(p):\n"
        "    return bounded(p)\n"
    )
    assert _violations({"m.py": src}) == []


def test_annotation_found_across_comment_block():
    src = (
        "async def h(p):\n"
        "    # loop-blocking-ok: two-line config file, microseconds —\n"
        "    # the continuation line explains the bound in detail.\n"
        "    return p.read_text()\n"
    )
    assert _violations({"m.py": src}) == []


def test_same_file_definition_shadows_cross_file_taint():
    # A clean local function must not be tainted by an unrelated
    # same-named function in another service.
    srcs = {
        "a.py": "def helper(p):\n    return p.read_text()\n",
        "b.py": ("def helper(x):\n    return x\nasync def h(x):\n    return helper(x)\n"),
    }
    assert _violations(srcs) == []


def test_cross_file_method_taint_still_caught():
    # No local definition → global name fallback; this is what catches
    # method calls on objects defined in another module.
    srcs = {
        "a.py": "class M:\n    def reload_all(self):\n        return scan_sessions()\n",
        "b.py": "async def h(m):\n    m.reload_all()\n",
    }
    v = _violations(srcs)
    assert len(v) == 1 and "reload_all" in v[0]
