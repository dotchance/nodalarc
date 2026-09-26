# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""FRR image contract: the FRR adapter's ``daemons`` file is the only daemon
selection. The image ships no daemons file of its own, edits none, and
refuses to start FRR when the mounted one is missing.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

IMAGE_DIR = Path(__file__).resolve().parents[2] / "images" / "frr"
ENTRYPOINT = IMAGE_DIR / "entrypoint.sh"


def _code_lines(text: str) -> list[str]:
    return [
        line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")
    ]


def test_image_ships_and_edits_no_daemon_selection() -> None:
    assert not (IMAGE_DIR / "daemons").exists()
    dockerfile = _code_lines((IMAGE_DIR / "Dockerfile").read_text())
    assert not any("daemons" in line for line in dockerfile if line.startswith("COPY"))
    entrypoint = _code_lines(ENTRYPOINT.read_text())
    assert not any("sed" in line and "daemons" in line for line in entrypoint)


def test_entrypoint_refuses_to_start_frr_without_the_adapter_daemons_file() -> None:
    code = _code_lines(ENTRYPOINT.read_text())

    check = next((i for i, line in enumerate(code) if "! -f /etc/frr-config/daemons" in line), None)
    handoff = next(i for i, line in enumerate(code) if "exec /usr/lib/frr/docker-start" in line)
    assert check is not None, "entrypoint must check for the mounted daemons file"
    assert check < handoff
    refusal = code[check : check + 4]
    assert any("ERROR" in line for line in refusal)
    assert any(line.strip() == "exit 1" for line in refusal)


# ---------------------------------------------------------------------------
# Boot configuration: applied once every selected daemon is up and mgmtd
# serves every selected daemon that takes its configuration through it
# ---------------------------------------------------------------------------

BOOT_MARK = "/var/run/frr/nodalarc-boot-config-applied"
PROFILE = IMAGE_DIR.parents[1] / "catalog" / "nodalarc" / "profiles" / "frr-router.yaml"

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


def test_frr_starts_without_the_boot_configuration_and_readiness_waits_for_it() -> None:
    code = _code_lines(ENTRYPOINT.read_text())
    handoff = next(i for i, line in enumerate(code) if "exec /usr/lib/frr/docker-start" in line)
    # Every mounted file but frr.conf is copied before FRR starts.
    copy_loop = next(i for i, line in enumerate(code) if "for source in /etc/frr-config/*" in line)
    assert '[ "$(basename "$source")" = "frr.conf" ] || cp' in code[copy_loop + 1]
    assert not any(line.strip() == "cp /etc/frr-config/* /etc/frr/" for line in code)
    # A restarted container starts without the previous container's marker.
    removal = code.index(f"rm -f {BOOT_MARK}")
    assert removal < handoff
    # The marker is written only after frr.conf applied cleanly.
    apply = _function("apply_boot_configuration")
    assert apply.index("if ! vtysh -b; then") < apply.index(f"touch {BOOT_MARK}")
    # The router is ready only once the marker exists.
    readiness = yaml.safe_load(PROFILE.read_text())["profile"]["readiness"]["argv"]
    assert readiness[-1].startswith(f"test -f {BOOT_MARK} && ")
