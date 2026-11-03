"""Write the backend facts the page's body-sample and satellite tests compare against.

For the shipped earth-luna-dtn session this builds the Builder world in-process, the way
VS-API builds it, and records:

- the SessionEphemeris and the body-sample window the page receives for the epoch;
- the next window OME publishes, so tests cross a window replacement;
- body and Sun states from the physics module at instants between samples;
- Earth and lunar satellite states from the shared satellite function at the same instants.

    uv run python scripts/gen_body_sample_fixture.py          # write
    uv run python scripts/gen_body_sample_fixture.py --check  # exit 1 when stale
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "services"))

from nodalarc.catalog_closure import FilesystemCatalogReadView  # noqa: E402
from nodalarc.catalog_paths import CatalogRoots  # noqa: E402
from nodalarc.configuration_yaml import load_configuration_yaml  # noqa: E402
from nodalarc.models.events import EphemerisNodeKeplerian  # noqa: E402
from nodalarc.orbital import OrbitalElements  # noqa: E402
from nodalarc.physics.datasets import convert_datasets, production_location  # noqa: E402
from nodalarc.physics.samples import body_sample_window, next_window_due_unix  # noqa: E402
from nodalarc.physics.satellites import analytic_satellite_states, orbit_model_for  # noqa: E402
from nodalarc.physics.session import body_frame_from_facts, session_physics  # noqa: E402
from nodalarc.platform_config import init_platform_config  # noqa: E402
from nodalarc.resolve_session import resolve_session_with_assets  # noqa: E402
from vs_api.builder_world import world_from_resolution  # noqa: E402

SHIPPED_ROOT = ROOT / "catalog/nodalarc"
SESSION = SHIPPED_ROOT / "sessions/earth-luna-dtn.yaml"
OUTPUT = ROOT / "frontend/src/sim/__tests__/fixtures/bodySamples.json"
SATELLITES_PER_BODY = 2
# An eccentric J2 orbit (the shipped Molniya element set) with elements valid at
# the session epoch: the regime where an argument-of-perigee or J2 sign error
# shows, which near-circular orbits hide.
MOLNIYA = {
    "type": "keplerian",
    "propagator": "j2-mean-elements",
    "semi_major_axis_km": 26521.0088,
    "eccentricity": 0.7371514465166197,
    "inclination_deg": 63.4,
    "raan_deg": 270.0,
    "argument_of_perigee_deg": 270.0,
    "mean_anomaly_deg": 0.0,
    "reference_body": "earth",
}


def _vector(v) -> list[float]:
    return [float(x) for x in v]


def render() -> str:
    init_platform_config(ROOT / "configs/platform.yaml")
    convert_datasets(production_location())
    document = load_configuration_yaml(SESSION.read_bytes())
    resolution = resolve_session_with_assets(
        document, catalog=FilesystemCatalogReadView(CatalogRoots.from_catalog_root(SHIPPED_ROOT))
    )
    world = world_from_resolution(resolution)
    resolved = resolution.resolved
    physics = session_physics(resolved)
    ephemeris = world.ephemeris
    epoch = ephemeris.epoch_unix
    next_window = body_sample_window(
        physics, epoch_id=ephemeris.epoch_id, produced_at_unix=next_window_due_unix(epoch)
    )

    # Instants between samples, from inside the first window across the
    # replacement into the second, never on the 600 s grid.
    start = world.body_samples.samples[0].sim_time_unix
    end = next_window.samples[-1].sim_time_unix
    instants = [start + 137.25 + k * 1234.5 for k in range(int((end - start - 137.25) / 1234.5))]

    bodies_truth = []
    for t in instants:
        bodies = {}
        for body_id in physics.body_ids:
            state = physics.body_state(body_id, t)
            orientation = physics.orientation(body_id, t)
            bodies[body_id] = {
                "position_km": _vector(state.position_km),
                "velocity_km_s": _vector(state.velocity_km_s),
                "rotation": [_vector(row) for row in orientation.rotation],
                "angular_velocity_rad_s": _vector(orientation.angular_velocity_rad_s),
            }
        sun = physics.sun_state(t)
        bodies_truth.append(
            {
                "sim_time_unix": t,
                "bodies": bodies,
                "sun": {
                    "position_km": _vector(sun.position_km),
                    "velocity_km_s": _vector(sun.velocity_km_s),
                },
            }
        )

    frames = {facts.body_id: body_frame_from_facts(facts) for facts in resolved.bodies}
    chosen: dict[str, list[tuple[str, EphemerisNodeKeplerian | dict]]] = {}
    for node_id, node in sorted(ephemeris.nodes.items()):
        if isinstance(node, EphemerisNodeKeplerian):
            members = chosen.setdefault(node.reference_body, [])
            if len(members) < SATELLITES_PER_BODY:
                members.append((node_id, node))
    chosen["earth"].append(("molniya-j2", MOLNIYA))
    satellites_truth = []
    for body_id, members in sorted(chosen.items()):
        for node_id, node in members:
            facts = node if isinstance(node, dict) else node.model_dump(mode="json")
            elements = OrbitalElements(
                semi_major_axis_km=facts["semi_major_axis_km"],
                inclination_rad=math.radians(facts["inclination_deg"]),
                raan_rad=math.radians(facts["raan_deg"]),
                mean_anomaly_rad=math.radians(facts["mean_anomaly_deg"]),
                eccentricity=facts["eccentricity"],
                argument_of_perigee_rad=math.radians(facts["argument_of_perigee_deg"]),
            )
            states = []
            for t in instants[::4]:
                result = analytic_satellite_states(
                    physics,
                    body_frame=frames[body_id],
                    model=orbit_model_for(facts["propagator"]),
                    elements=[elements],
                    phases_s=[t - epoch],
                    master_sim_time_unix=t,
                )
                states.append(
                    {
                        "sim_time_unix": t,
                        "position_icrf_km": _vector(result.position_icrf_km[0]),
                        "position_fixed_km": _vector(result.position_fixed_km[0]),
                        "velocity_fixed_km_s": _vector(result.velocity_fixed_km_s[0]),
                    }
                )
            satellites_truth.append(
                {"node_id": node_id, "body_id": body_id, "elements": facts, "states": states}
            )

    fixture = {
        "source": "catalog/nodalarc/sessions/earth-luna-dtn.yaml",
        "ephemeris": ephemeris.model_dump(mode="json"),
        "windows": [
            world.body_samples.model_dump(mode="json"),
            next_window.model_dump(mode="json"),
        ],
        "bodies_truth": bodies_truth,
        "satellites_truth": satellites_truth,
    }
    return json.dumps(fixture, indent=1, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="exit 1 when the file is stale")
    args = parser.parse_args()
    text = render()
    if args.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else None
        if current != text:
            print(f"{OUTPUT.relative_to(ROOT)} is stale; run scripts/gen_body_sample_fixture.py")
            return 1
        print(f"{OUTPUT.relative_to(ROOT)} is current.")
        return 0
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
