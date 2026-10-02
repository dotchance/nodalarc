# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""The FRR image entrypoint applies the boot configuration once every selected daemon is up
and connected, and ends the container when that cannot happen."""

from __future__ import annotations

import subprocess
from pathlib import Path

IMAGE_DIR = Path(__file__).resolve().parents[3] / "images" / "frr"
ENTRYPOINT = IMAGE_DIR / "entrypoint.sh"


# ---------------------------------------------------------------------------
# Boot configuration: applied once every selected daemon is up and mgmtd
# serves every selected daemon that takes its configuration through it
# ---------------------------------------------------------------------------


WATCHFRR_UP = """watchfrr global phase: Idle
 Reading Configuration: no
  zebra                Up
  mgmtd                Up
  isisd                Up
  staticd              Up
"""
BACKENDS_CONNECTED = """MGMTD Backend Adapters
  Client: \t\t\tzebra
    Client-Id: \t\t\t1
  Client: \t\t\tstaticd
    Client-Id: \t\t\t4
  Total: 2
"""
SELECTED = "mgmtd=yes\nzebra=yes\nbgpd=no\nripd=no\nisisd=yes\nstaticd=yes\n"


def _function(name: str) -> str:
    text = ENTRYPOINT.read_text()
    start = text.index(f"{name}() {{")
    return text[start : text.index("\n}\n", start) + 3]


def _waits_for(tmp_path: Path, *, daemons: str, watchfrr: str | None, backends: str | None) -> str:
    """Run the entrypoint's own wait check against a stand-in vtysh."""
    (tmp_path / "daemons").write_text(daemons)
    for name, output in (("watchfrr", watchfrr), ("backends", backends)):
        if output is not None:
            (tmp_path / name).write_text(output)
    stub = f"""
vtysh() {{
    case "$2" in
        'show watchfrr') f={tmp_path}/watchfrr ;;
        'show mgmt backend-adapter all') f={tmp_path}/backends ;;
        *) return 2 ;;
    esac
    [ -f "$f" ] || {{ echo "vtysh: failed to connect" >&2; return 1; }}
    cat "$f"
}}
"""
    mgmtd_backends = next(
        line for line in ENTRYPOINT.read_text().splitlines() if line.startswith("MGMTD_BACKENDS=")
    )
    script = (
        f"set -e\n{mgmtd_backends}\n{stub}\n{_function('boot_configuration_waits_for')}\n"
        f"boot_configuration_waits_for {tmp_path}/daemons\n"
    )
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True)
    return result.stdout


def test_nothing_is_waited_for_once_daemons_are_up_and_backends_connected(tmp_path) -> None:
    waits = _waits_for(
        tmp_path, daemons=SELECTED, watchfrr=WATCHFRR_UP, backends=BACKENDS_CONNECTED
    )
    assert waits == ""


def test_a_selected_daemon_not_connected_to_mgmtd_is_waited_for(tmp_path) -> None:
    only_staticd = BACKENDS_CONNECTED.replace("Client: \t\t\tzebra", "Client: \t\t\tother")
    waits = _waits_for(tmp_path, daemons=SELECTED, watchfrr=WATCHFRR_UP, backends=only_staticd)
    assert waits == " zebra(no mgmtd backend)"


def test_a_selected_daemon_that_is_not_up_is_waited_for(tmp_path) -> None:
    isisd_down = WATCHFRR_UP.replace("isisd                Up", "isisd                Down")
    waits = _waits_for(tmp_path, daemons=SELECTED, watchfrr=isisd_down, backends=BACKENDS_CONNECTED)
    assert waits == " isisd(down)"


def test_before_frr_answers_every_selected_daemon_is_waited_for(tmp_path) -> None:
    waits = _waits_for(tmp_path, daemons=SELECTED, watchfrr=None, backends=None)
    assert waits == (
        " mgmtd(down) zebra(down) zebra(no mgmtd backend) isisd(down)"
        " staticd(down) staticd(no mgmtd backend)"
    )


def test_a_selected_mgmtd_backend_beyond_zebra_and_staticd_is_waited_for(tmp_path) -> None:
    with_ripd = SELECTED.replace("ripd=no", "ripd=yes")
    ripd_up = WATCHFRR_UP + "  ripd                 Up\n"
    waits = _waits_for(tmp_path, daemons=with_ripd, watchfrr=ripd_up, backends=BACKENDS_CONNECTED)
    assert waits == " ripd(no mgmtd backend)"


def _boot_outcome(*, waits_for: str, vtysh_b_status: int) -> subprocess.CompletedProcess[str]:
    """Run the entrypoint's own boot helper the way the entrypoint does: in the
    background, while the entrypoint's process becomes a long-running program."""
    script = f"""
set -e
BOOT_WAIT_S=0
cp() {{ :; }}
chown() {{ :; }}
touch() {{ :; }}
vtysh() {{ return {vtysh_b_status}; }}
boot_configuration_waits_for() {{ printf '%s' '{waits_for}'; }}
{_function("apply_boot_configuration")}
apply_boot_configuration &
# Test stand-in for the entrypoint's last line, exec /usr/lib/frr/docker-start:
# a long-running process the helper's TERM must end at once.
exec sleep 30
"""
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=20)


def test_a_boot_configuration_that_does_not_apply_ends_the_container() -> None:
    outcome = _boot_outcome(waits_for="", vtysh_b_status=1)
    # The process the entrypoint became ended on TERM, well before its 30 s.
    assert outcome.returncode == -15
    assert "ERROR: frr.conf did not apply cleanly; ending the container" in outcome.stderr


def test_daemons_that_never_come_up_end_the_container() -> None:
    outcome = _boot_outcome(waits_for=" zebra(no mgmtd backend)", vtysh_b_status=0)
    assert outcome.returncode == -15
    assert "still waiting for: zebra(no mgmtd backend); ending the container" in outcome.stderr
