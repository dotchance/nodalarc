from __future__ import annotations

import ipaddress
import math
import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "catalog" / "nodalarc"
SESSIONS = CATALOG / "sessions"
CONFIGS = ROOT / "configs"

IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
URL_RE = re.compile(r"^https://")

TERMINAL_MEDIA = {"rf", "optical"}
MOUNT_ROLES = {"access", "isl", "crosslink", "backbone"}
NODE_SELECTOR_LEAVES = {"segment", "tag", "node", "plane", "slot"}
TERMINAL_SELECTOR_LEAVES = {"role", "medium", "mount"}
FORWARDING_CLASSES = {"routed", "host", "bridge", "control_only"}
# "crtbp" is structurally valid grammar (NRHO/halo three-body trajectories);
# the runtime-support layer rejects it with a typed UnsupportedFeature.
PROPAGATORS = {"two_body", "j2_mean_elements", "sgp4_tle", "crtbp"}
PHASING_MODES = {"walker_delta", "walker_star", "evenly_spaced_mean_anomaly"}
BORESIGHT_MODES = {"local_vertical", "configured_topocentric", "steerable_envelope"}
SELECTION_POLICIES = {"highest_elevation", "lowest_elevation", "longest_remaining_pass"}
HANDOVER_POLICIES = {"hysteresis", "hard_release"}
HANDOVER_MODES = {"mbb", "bbm"}
HANDOVER_CONCURRENCY = {"one_at_a_time", "all_at_once"}
RANKING_COMPONENTS = {
    "service_priority",
    "selection_score",
    "per_gs_rank",
    "satellite_ground_terminal_capacity",
    "lex_pair",
}
TOPOLOGY_MODES = {"visible_candidates", "nearest_n", "nearest_visible", "explicit_pairs"}
ROUTING_PROTOCOLS = {"isis", "ospf", "bgp", "static"}
ROUTING_ADAPTERS = {"static_ip", "bgp", "dtn_bundle"}
AGGREGATE_SOURCES = {"originated"}
ALLOCATION_MODES = {"by_node_order", "by_attach_index", "by_plane_slot", "by_ground_index"}

OBSOLETE_CONFIG_ROOTS = [
    "constellations",
    "ground-stations",
    "presets",
    "satellite-types",
    "scenarios",
    "sessions",
]


def _load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _yaml_files(root: Path) -> list[Path]:
    return sorted(path for path in root.glob("**/*.yaml") if path.is_file())


def _catalog_files() -> list[Path]:
    # Sessions live under the catalog (catalog/nodalarc/sessions/) but are validated
    # as sessions, not primitives — exclude that subtree from primitive checks.
    return [path for path in _yaml_files(CATALOG) if SESSIONS not in path.parents]


def _session_files() -> list[Path]:
    return _yaml_files(SESSIONS)


def _single_wrapper(path: Path) -> tuple[str, dict[str, Any]]:
    data = _load(path)
    assert len(data) == 1, path
    wrapper = next(iter(data))
    body = data[wrapper]
    assert isinstance(body, dict), path
    return wrapper, body


def _assert_identifier(value: Any, owner: Path, label: str) -> None:
    assert isinstance(value, str) and IDENTIFIER_RE.fullmatch(value), (owner, label, value)


def _assert_url(value: Any, owner: Path, label: str) -> None:
    assert isinstance(value, str) and URL_RE.match(value), (owner, label, value)


def _assert_tags(value: Any, owner: Path, label: str) -> None:
    assert isinstance(value, list), (owner, label, value)
    for tag in value:
        _assert_identifier(tag, owner, label)
    assert len(value) == len(set(value)), (owner, label, value)


def _assert_finite_number(value: Any, owner: Path, label: str) -> None:
    assert isinstance(value, int | float) and not isinstance(value, bool), (owner, label, value)
    assert math.isfinite(value), (owner, label, value)


def _assert_positive_number(value: Any, owner: Path, label: str) -> None:
    _assert_finite_number(value, owner, label)
    assert value > 0, (owner, label, value)


def _assert_nonnegative_number(value: Any, owner: Path, label: str) -> None:
    _assert_finite_number(value, owner, label)
    assert value >= 0, (owner, label, value)


def _assert_positive_int(value: Any, owner: Path, label: str) -> None:
    assert isinstance(value, int) and not isinstance(value, bool) and value > 0, (
        owner,
        label,
        value,
    )


def _assert_nonnegative_int(value: Any, owner: Path, label: str) -> None:
    assert isinstance(value, int) and not isinstance(value, bool) and value >= 0, (
        owner,
        label,
        value,
    )


def _assert_exact_keys(
    value: dict[str, Any],
    required: set[str],
    optional: set[str],
    owner: Path,
    label: str,
) -> None:
    keys = set(value)
    assert required <= keys, (owner, label, "missing", sorted(required - keys))
    assert keys <= required | optional, (owner, label, "extra", sorted(keys - required - optional))


def _assert_unique(values: list[str], owner: Path, label: str) -> None:
    assert len(values) == len(set(values)), (owner, label, values)


def _cidr(value: str, owner: Path, label: str) -> ipaddress._BaseNetwork:
    assert isinstance(value, str) and "/" in value, (owner, label, value)
    return ipaddress.ip_network(value, strict=True)


def _interface(value: str, owner: Path, label: str) -> ipaddress._BaseInterface:
    assert isinstance(value, str) and "/" in value, (owner, label, value)
    return ipaddress.ip_interface(value)


def _catalog_ref_path(value: Any, owner: Path, expected_wrapper: str | None = None) -> Path:
    assert isinstance(value, str) and value.startswith("nodalarc:"), (owner, value)
    path = CATALOG / value.split(":", 1)[1]
    assert path.exists(), (owner, value, path)
    if expected_wrapper is not None:
        wrapper, _ = _single_wrapper(path)
        assert wrapper == expected_wrapper, (owner, value, wrapper, expected_wrapper)
    return path


def _collect_refs(value: Any) -> list[str]:
    refs: list[str] = []
    if isinstance(value, dict):
        for child in value.values():
            refs.extend(_collect_refs(child))
    elif isinstance(value, list):
        for child in value:
            refs.extend(_collect_refs(child))
    elif isinstance(value, str) and value.startswith("nodalarc:"):
        refs.append(value)
    return refs


def _terminal_by_ref(value: str, owner: Path) -> dict[str, Any]:
    path = _catalog_ref_path(value, owner, "terminal")
    return _load(path)["terminal"]


def _node_by_ref(value: str, owner: Path) -> dict[str, Any]:
    path = _catalog_ref_path(value, owner, "node")
    return _load(path)["node"]


def _body_by_ref(value: str, owner: Path) -> dict[str, Any]:
    path = _catalog_ref_path(value, owner, "body")
    return _load(path)["body"]


def _site_by_ref(value: str, owner: Path) -> dict[str, Any]:
    path = _catalog_ref_path(value, owner, "site")
    return _load(path)["site"]


def _site_set_by_ref(value: str, owner: Path) -> dict[str, Any]:
    path = _catalog_ref_path(value, owner, "site_set")
    return _load(path)["site_set"]


def _assert_angle_range(value: Any, owner: Path, label: str, *, elevation: bool = False) -> None:
    assert isinstance(value, dict), (owner, label, value)
    assert set(value) == {"min", "max"}, (owner, label, value)
    _assert_finite_number(value["min"], owner, f"{label}.min")
    _assert_finite_number(value["max"], owner, f"{label}.max")
    assert value["min"] <= value["max"], (owner, label, value)
    if elevation:
        assert -90 <= value["min"] <= 90, (owner, label, value)
        assert -90 <= value["max"] <= 90, (owner, label, value)


def _assert_directional_bandwidth(value: Any, owner: Path, label: str) -> None:
    assert isinstance(value, dict), (owner, label, value)
    assert set(value) == {"transmit", "receive"}, (owner, label, value)
    _assert_nonnegative_number(value["transmit"], owner, f"{label}.transmit")
    _assert_nonnegative_number(value["receive"], owner, f"{label}.receive")


def _assert_terminal_limits(value: Any, owner: Path, label: str) -> None:
    assert isinstance(value, dict), (owner, label, value)
    assert set(value) == {"azimuth_deg", "elevation_deg", "max_tracking_rate_deg_s"}, (
        owner,
        label,
        value,
    )
    _assert_angle_range(value["azimuth_deg"], owner, f"{label}.azimuth_deg")
    _assert_angle_range(value["elevation_deg"], owner, f"{label}.elevation_deg", elevation=True)
    _assert_positive_number(value["max_tracking_rate_deg_s"], owner, f"{label}.max_tracking_rate")


def _assert_boresight(value: Any, owner: Path, label: str) -> None:
    assert isinstance(value, dict), (owner, label, value)
    mode = value.get("mode")
    assert mode in BORESIGHT_MODES, (owner, label, value)
    if mode == "local_vertical":
        assert set(value) == {"mode"}, (owner, label, value)
    elif mode == "configured_topocentric":
        assert set(value) == {"mode", "azimuth_deg", "elevation_deg"}, (owner, label, value)
        _assert_finite_number(value["azimuth_deg"], owner, f"{label}.azimuth_deg")
        _assert_finite_number(value["elevation_deg"], owner, f"{label}.elevation_deg")
    else:
        assert set(value) == {"mode", "azimuth_deg", "elevation_deg"}, (owner, label, value)
        _assert_angle_range(value["azimuth_deg"], owner, f"{label}.azimuth_deg")
        _assert_angle_range(value["elevation_deg"], owner, f"{label}.elevation_deg", elevation=True)


def _assert_orbit(value: Any, owner: Path, label: str = "orbit") -> None:
    if isinstance(value, str):
        _catalog_ref_path(value, owner, "orbit")
        return
    assert isinstance(value, dict), (owner, label, value)
    _assert_exact_keys(
        value,
        {"id", "central_body", "epoch", "orientation", "phase", "propagator", "reference"},
        {"elements", "shape", "notes"},
        owner,
        label,
    )
    _assert_identifier(value["id"], owner, f"{label}.id")
    _catalog_ref_path(value["central_body"], owner, "body")
    assert isinstance(value["epoch"], str) and value["epoch"], (owner, label, value)
    assert ("elements" in value) ^ ("shape" in value), (owner, label, value)
    if "elements" in value:
        elements = value["elements"]
        assert set(elements) == {"semi_major_axis_km", "eccentricity"}, (owner, label, elements)
        _assert_positive_number(elements["semi_major_axis_km"], owner, f"{label}.semi_major_axis")
        _assert_nonnegative_number(elements["eccentricity"], owner, f"{label}.eccentricity")
    else:
        shape = value["shape"]
        assert isinstance(shape, dict), (owner, label, shape)
        if "altitude_km" in shape:
            assert set(shape) == {"altitude_km"}, (owner, label, shape)
            _assert_positive_number(shape["altitude_km"], owner, f"{label}.altitude_km")
        else:
            assert set(shape) == {"perigee_altitude_km", "apogee_altitude_km"}, (
                owner,
                label,
                shape,
            )
            _assert_positive_number(shape["perigee_altitude_km"], owner, f"{label}.perigee")
            _assert_positive_number(shape["apogee_altitude_km"], owner, f"{label}.apogee")
            assert shape["perigee_altitude_km"] < shape["apogee_altitude_km"], (
                owner,
                label,
                shape,
            )
    orientation = value["orientation"]
    assert set(orientation) == {"inclination_deg", "raan_deg", "argument_of_perigee_deg"}, (
        owner,
        label,
        orientation,
    )
    for key in orientation:
        _assert_finite_number(orientation[key], owner, f"{label}.orientation.{key}")
    phase = value["phase"]
    assert set(phase) == {"mean_anomaly_deg"}, (owner, label, phase)
    _assert_finite_number(phase["mean_anomaly_deg"], owner, f"{label}.phase.mean_anomaly_deg")
    assert value["propagator"] in PROPAGATORS, (owner, label, value)
    _assert_url(value["reference"], owner, f"{label}.reference")


def _assert_node_selector(
    value: Any, owner: Path, label: str, segment_ids: set[str] | None
) -> None:
    _assert_set_expression(value, owner, label, NODE_SELECTOR_LEAVES, segment_ids=segment_ids)


def _assert_terminal_selector(value: Any, owner: Path, label: str) -> None:
    _assert_set_expression(value, owner, label, TERMINAL_SELECTOR_LEAVES, segment_ids=None)


def _assert_set_expression(
    value: Any,
    owner: Path,
    label: str,
    leaves: set[str],
    *,
    segment_ids: set[str] | None,
) -> None:
    assert isinstance(value, dict), (owner, label, value)
    assert len(value) == 1, (owner, label, value)
    key = next(iter(value))
    operand = value[key]

    if key in {"all", "any"}:
        assert isinstance(operand, list) and operand, (owner, label, value)
        for index, child in enumerate(operand):
            _assert_set_expression(
                child, owner, f"{label}.{key}[{index}]", leaves, segment_ids=segment_ids
            )
        return

    if key == "not":
        _assert_set_expression(operand, owner, f"{label}.not", leaves, segment_ids=segment_ids)
        return

    assert key in leaves, (owner, label, key, leaves)
    if key in {"segment", "tag", "node", "mount"}:
        _assert_identifier(operand, owner, f"{label}.{key}")
        if key == "segment" and segment_ids is not None:
            assert operand in segment_ids, (owner, label, operand, segment_ids)
    elif key in {"plane", "slot"}:
        _assert_nonnegative_int(operand, owner, f"{label}.{key}")
    elif key == "role":
        assert operand in MOUNT_ROLES, (owner, label, operand)
    elif key == "medium":
        assert operand in TERMINAL_MEDIA, (owner, label, operand)


def _assert_scheduling(value: Any, owner: Path, label: str) -> None:
    assert isinstance(value, dict), (owner, label, value)
    _assert_exact_keys(
        value,
        set(),
        {
            "selection_policy",
            "handover_policy",
            "handover_mode",
            "mbb_overlap_ticks",
            "mbb_reserve",
            "handover_concurrency",
            "ranking_order",
            "mbb_preemption",
            "successor_abort_policy",
            "cross_tenant_displacement",
            "bbm_acquire_timeout_ticks",
        },
        owner,
        label,
    )
    if "selection_policy" in value:
        policy = value["selection_policy"]
        assert isinstance(policy, dict) and len(policy) == 1, (owner, label, policy)
        name = next(iter(policy))
        assert name in SELECTION_POLICIES, (owner, label, policy)
        params = policy[name]
        if name in {"highest_elevation", "lowest_elevation"}:
            assert params == {}, (owner, label, policy)
        else:
            assert set(params) == {"lookahead_horizon_ticks"}, (owner, label, policy)
            _assert_positive_int(params["lookahead_horizon_ticks"], owner, label)
    if "handover_policy" in value:
        policy = value["handover_policy"]
        assert isinstance(policy, dict) and len(policy) == 1, (owner, label, policy)
        name = next(iter(policy))
        assert name in HANDOVER_POLICIES, (owner, label, policy)
        params = policy[name]
        if name == "hard_release":
            assert params == {}, (owner, label, policy)
        else:
            assert set(params) == {"discount_factor", "mask_fade_range_deg"}, (owner, label, policy)
            _assert_positive_number(params["discount_factor"], owner, label)
            _assert_nonnegative_number(params["mask_fade_range_deg"], owner, label)
    if "handover_mode" in value:
        assert value["handover_mode"] in HANDOVER_MODES, (owner, label, value)
    for key in ("mbb_overlap_ticks", "mbb_reserve", "bbm_acquire_timeout_ticks"):
        if key in value:
            _assert_nonnegative_int(value[key], owner, f"{label}.{key}")
    if "handover_concurrency" in value:
        assert value["handover_concurrency"] in HANDOVER_CONCURRENCY, (owner, label, value)
    if "ranking_order" in value:
        assert isinstance(value["ranking_order"], list) and value["ranking_order"], (
            owner,
            label,
            value,
        )
        for item in value["ranking_order"]:
            assert item in RANKING_COMPONENTS, (owner, label, item)
        _assert_unique(value["ranking_order"], owner, f"{label}.ranking_order")
    if "mbb_preemption" in value:
        assert value["mbb_preemption"] == "off", (owner, label, value)
    if "successor_abort_policy" in value:
        assert value["successor_abort_policy"] in {"hard_release", "soft_retain"}, (
            owner,
            label,
            value,
        )
    if "cross_tenant_displacement" in value:
        assert value["cross_tenant_displacement"] == "off", (owner, label, value)


def test_no_user_catalog_or_obsolete_example_roots_remain() -> None:
    assert not (ROOT / "catalog" / "user").exists()
    assert not (ROOT / "sessions").exists()  # sessions are folded into catalog/nodalarc/sessions
    for child in OBSOLETE_CONFIG_ROOTS:
        assert not (CONFIGS / child).exists(), child


def test_catalog_and_session_references_resolve() -> None:
    files = _catalog_files() + _session_files()
    assert files

    refs: list[tuple[Path, str]] = []
    for path in files:
        refs.extend((path, ref) for ref in _collect_refs(_load(path)))

    assert refs
    for owner, value in refs:
        _catalog_ref_path(value, owner)


def test_catalog_uses_one_wrapper_per_file_and_no_legacy_shapes() -> None:
    allowed = {
        "body",
        "terminal",
        "orbit",
        "payload",
        "node",
        "site",
        "site_set",
        "constellation",
        "space_node",
        "space_node_set",
    }
    forbidden = {
        "ground_station",
        "satellite_type",
        "terminal_instances",
        "payload_instances",
        "visibility",
        "namespace",
        "kind",
    }

    for path in _catalog_files():
        wrapper, body = _single_wrapper(path)
        assert wrapper in allowed, path
        assert body["id"] == path.stem, path
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert f"{token}:" not in text, (path, token)


def test_body_primitives_have_identical_required_physical_fields() -> None:
    paths = _yaml_files(CATALOG / "bodies")
    assert paths
    expected_keys = {
        "id",
        "display_name",
        "gravitational_parameter_km3_s2",
        "mean_radius_km",
        "equatorial_radius_km",
        "polar_radius_km",
        "reference",
        "notes",
    }
    for path in paths:
        wrapper, body = _single_wrapper(path)
        assert wrapper == "body", path
        assert set(body) == expected_keys, path
        _assert_identifier(body["id"], path, "id")
        for key in (
            "gravitational_parameter_km3_s2",
            "mean_radius_km",
            "equatorial_radius_km",
            "polar_radius_km",
        ):
            _assert_positive_number(body[key], path, key)
        _assert_url(body["reference"], path, "reference")


def test_terminal_primitives_match_the_terminal_grammar() -> None:
    paths = _yaml_files(CATALOG / "terminals")
    assert paths
    ids: list[str] = []

    for path in paths:
        wrapper, terminal = _single_wrapper(path)
        assert wrapper == "terminal", path
        ids.append(terminal["id"])
        _assert_exact_keys(
            terminal,
            {
                "id",
                "display_name",
                "medium",
                "signal",
                "bandwidth_mbps",
                "tracking_capacity",
                "max_range_km",
                "limits",
                "reference",
            },
            {"notes"},
            path,
            "terminal",
        )
        _assert_identifier(terminal["id"], path, "id")
        assert terminal["medium"] in TERMINAL_MEDIA, path
        signal = terminal["signal"]
        if terminal["medium"] == "rf":
            assert set(signal) == {"band", "frequency_hz"}, (path, signal)
            _assert_identifier(signal["band"], path, "signal.band")
            _assert_positive_number(signal["frequency_hz"], path, "signal.frequency_hz")
        else:
            assert set(signal) == {"wavelength_nm"}, (path, signal)
            _assert_positive_number(signal["wavelength_nm"], path, "signal.wavelength_nm")
        _assert_directional_bandwidth(terminal["bandwidth_mbps"], path, "bandwidth_mbps")
        _assert_positive_int(terminal["tracking_capacity"], path, "tracking_capacity")
        _assert_positive_number(terminal["max_range_km"], path, "max_range_km")
        _assert_terminal_limits(terminal["limits"], path, "limits")
        _assert_url(terminal["reference"], path, "reference")

    _assert_unique(ids, CATALOG / "terminals", "terminal ids")


def test_node_primitives_reference_terminal_mounts_without_site_inventory() -> None:
    paths = _yaml_files(CATALOG / "nodes")
    assert paths
    ids: list[str] = []

    for path in paths:
        wrapper, node = _single_wrapper(path)
        assert wrapper == "node", path
        ids.append(node["id"])
        _assert_exact_keys(
            node,
            {"id", "forwarding", "ethernet", "terminals", "payloads"},
            {"display_name", "tags", "reference", "notes"},
            path,
            "node",
        )
        _assert_identifier(node["id"], path, "id")
        assert node["forwarding"] in FORWARDING_CLASSES, path
        if "tags" in node:
            _assert_tags(node["tags"], path, "tags")
        if "reference" in node:
            _assert_url(node["reference"], path, "reference")

        assert isinstance(node["ethernet"], list), path
        ethernet_ids = []
        for port in node["ethernet"]:
            _assert_exact_keys(port, {"id"}, {"tags"}, path, "ethernet")
            _assert_identifier(port["id"], path, "ethernet.id")
            ethernet_ids.append(port["id"])
            if "tags" in port:
                _assert_tags(port["tags"], path, "ethernet.tags")
        _assert_unique(ethernet_ids, path, "ethernet ids")

        assert isinstance(node["terminals"], list), path
        mount_ids = []
        for mount in node["terminals"]:
            _assert_exact_keys(mount, {"id", "role", "terminal", "count"}, {"tags"}, path, "mount")
            _assert_identifier(mount["id"], path, "mount.id")
            mount_ids.append(mount["id"])
            assert mount["role"] in MOUNT_ROLES, (path, mount)
            _catalog_ref_path(mount["terminal"], path, "terminal")
            _assert_positive_int(mount["count"], path, "mount.count")
            if "tags" in mount:
                _assert_tags(mount["tags"], path, "mount.tags")
        _assert_unique(mount_ids, path, "terminal mount ids")

        assert isinstance(node["payloads"], list), path
        for mount in node["payloads"]:
            _assert_exact_keys(mount, {"id", "payload", "count"}, {"tags"}, path, "payload")
            _assert_identifier(mount["id"], path, "payload.id")
            _catalog_ref_path(mount["payload"], path, "payload")
            _assert_positive_int(mount["count"], path, "payload.count")

    _assert_unique(ids, CATALOG / "nodes", "node ids")


def test_site_primitives_obey_site_and_installation_contracts() -> None:
    paths = _yaml_files(CATALOG / "sites")
    assert paths
    ids: list[str] = []

    for path in paths:
        wrapper, site = _single_wrapper(path)
        assert wrapper == "site", path
        ids.append(site["id"])
        _assert_exact_keys(
            site,
            {"id", "lan", "nodes", "frame", "location"},
            {"display_name", "verified", "tags"},
            path,
            "site",
        )
        _assert_identifier(site["id"], path, "site.id")
        if "tags" in site:
            _assert_tags(site["tags"], path, "site.tags")
        if "verified" in site and "notes" in site["verified"]:
            notes = site["verified"]["notes"].lower()
            for forbidden in ("city-center", "city-level", "public city/location", "town model"):
                assert forbidden not in notes, (path, forbidden, site["verified"]["notes"])

        frame = site["frame"]
        assert set(frame) == {"body_fixed"}, (path, frame)
        assert set(frame["body_fixed"]) == {"body"}, (path, frame)
        _catalog_ref_path(frame["body_fixed"]["body"], path, "body")

        location = site["location"]
        assert set(location) == {"lat_deg", "lon_deg", "alt_m"}, (path, location)
        for key in location:
            _assert_finite_number(location[key], path, f"location.{key}")

        lan = site["lan"]
        assert set(lan) <= {"ipv4", "ipv6"} and lan, (path, lan)
        lan_networks = {
            family: _cidr(prefix, path, f"lan.{family}") for family, prefix in lan.items()
        }

        assert isinstance(site["nodes"], list) and site["nodes"], path
        node_ids: list[str] = []
        for site_node in site["nodes"]:
            _assert_site_node(site_node, path, lan_networks)
            node_ids.append(site_node["id"])
        _assert_unique(node_ids, path, "site node ids")

    _assert_unique(ids, CATALOG / "sites", "site ids")


def _assert_site_node(
    site_node: dict[str, Any],
    path: Path,
    lan_networks: dict[str, ipaddress._BaseNetwork],
) -> None:
    _assert_exact_keys(
        site_node,
        {"id", "model", "terminals", "payloads", "interfaces"},
        {
            "display_name",
            "originated_prefixes",
            "tenant_id",
            "service_priority",
            "scheduling",
            "tags",
        },
        path,
        "site_node",
    )
    _assert_identifier(site_node["id"], path, "site_node.id")
    if "tags" in site_node:
        _assert_tags(site_node["tags"], path, "site_node.tags")
    if "scheduling" in site_node:
        _assert_scheduling(site_node["scheduling"], path, "site_node.scheduling")

    node_model = _node_by_ref(site_node["model"], path)
    model_mounts = {mount["id"]: mount for mount in node_model["terminals"]}

    assert isinstance(site_node["terminals"], dict), (path, site_node)
    for mount_id, installation in site_node["terminals"].items():
        _assert_identifier(mount_id, path, "terminal mount id")
        assert mount_id in model_mounts, (path, site_node["model"], mount_id)
        _assert_terminal_installation(installation, model_mounts[mount_id], path, mount_id)

    assert isinstance(site_node["payloads"], dict), (path, site_node)

    interfaces = site_node["interfaces"]
    assert set(interfaces) == {"lo0", "terr0"}, (path, interfaces)
    for iface_name, address in interfaces.items():
        assert set(address) <= {"ipv4", "ipv6"} and address, (path, iface_name, address)
        if iface_name == "terr0":
            assert set(address) == set(lan_networks), (path, iface_name, address, lan_networks)
        for family, value in address.items():
            interface = _interface(value, path, f"{site_node['id']}.{iface_name}.{family}")
            if iface_name == "terr0":
                assert interface.ip in lan_networks[family], (path, iface_name, value, lan_networks)

    if "originated_prefixes" in site_node:
        _assert_originated_prefixes(site_node["originated_prefixes"], path, site_node["id"])


def _assert_terminal_installation(
    installation: Any,
    mount: dict[str, Any],
    path: Path,
    mount_id: str,
) -> None:
    assert isinstance(installation, dict), (path, mount_id, installation)
    _assert_exact_keys(installation, {"installed_count"}, {"capabilities", "tags"}, path, mount_id)
    _assert_positive_int(installation["installed_count"], path, f"{mount_id}.installed_count")
    assert installation["installed_count"] <= mount["count"], (path, mount_id, installation, mount)
    if "tags" in installation:
        _assert_tags(installation["tags"], path, f"{mount_id}.tags")
    if "capabilities" not in installation:
        return

    terminal = _terminal_by_ref(mount["terminal"], path)
    capabilities = installation["capabilities"]
    _assert_exact_keys(
        capabilities,
        set(),
        {"bandwidth_mbps", "tracking_capacity", "max_range_km", "limits", "boresight"},
        path,
        f"{mount_id}.capabilities",
    )
    assert capabilities, (path, mount_id, capabilities)
    if "bandwidth_mbps" in capabilities:
        _assert_directional_bandwidth(capabilities["bandwidth_mbps"], path, f"{mount_id}.bandwidth")
        assert (
            capabilities["bandwidth_mbps"]["transmit"] <= terminal["bandwidth_mbps"]["transmit"]
        ), (
            path,
            mount_id,
        )
        assert capabilities["bandwidth_mbps"]["receive"] <= terminal["bandwidth_mbps"]["receive"], (
            path,
            mount_id,
        )
    if "tracking_capacity" in capabilities:
        _assert_positive_int(
            capabilities["tracking_capacity"], path, f"{mount_id}.tracking_capacity"
        )
        assert capabilities["tracking_capacity"] <= terminal["tracking_capacity"], (path, mount_id)
    if "max_range_km" in capabilities:
        _assert_positive_number(capabilities["max_range_km"], path, f"{mount_id}.max_range")
        assert capabilities["max_range_km"] <= terminal["max_range_km"], (path, mount_id)
    if "limits" in capabilities:
        _assert_terminal_limits(capabilities["limits"], path, f"{mount_id}.limits")
        for axis in ("azimuth_deg", "elevation_deg"):
            assert capabilities["limits"][axis]["min"] >= terminal["limits"][axis]["min"], (
                path,
                mount_id,
                axis,
            )
            assert capabilities["limits"][axis]["max"] <= terminal["limits"][axis]["max"], (
                path,
                mount_id,
                axis,
            )
        assert (
            capabilities["limits"]["max_tracking_rate_deg_s"]
            <= terminal["limits"]["max_tracking_rate_deg_s"]
        ), (path, mount_id)
    if "boresight" in capabilities:
        _assert_boresight(capabilities["boresight"], path, f"{mount_id}.boresight")


def _assert_originated_prefixes(
    value: Any,
    owner: Path,
    label: str,
) -> None:
    assert isinstance(value, dict), (owner, label, value)
    assert set(value) <= {"ipv4", "ipv6"} and value, (owner, label, value)
    for family, prefixes in value.items():
        assert isinstance(prefixes, list) and prefixes, (owner, label, value)
        for prefix in prefixes:
            network = _cidr(prefix, owner, f"{label}.originated.{family}")
            expected_version = 4 if family == "ipv4" else 6
            assert network.version == expected_version, (owner, label, prefix)


def test_site_sets_resolve_real_sites() -> None:
    paths = _yaml_files(CATALOG / "site-sets")
    assert paths
    for path in paths:
        wrapper, site_set = _single_wrapper(path)
        assert wrapper == "site_set", path
        _assert_exact_keys(
            site_set,
            {"id", "sites"},
            {"display_name", "tags", "reference", "notes"},
            path,
            "site_set",
        )
        _assert_identifier(site_set["id"], path, "site_set.id")
        assert isinstance(site_set["sites"], list) and site_set["sites"], path
        for site in site_set["sites"]:
            _catalog_ref_path(site, path, "site")
        _assert_unique(site_set["sites"], path, "site refs")
        if "tags" in site_set:
            _assert_tags(site_set["tags"], path, "site_set.tags")


def test_orbit_and_constellation_primitives_match_their_grammar() -> None:
    for path in _yaml_files(CATALOG / "orbits"):
        wrapper, orbit = _single_wrapper(path)
        assert wrapper == "orbit", path
        _assert_orbit(orbit, path)

    for path in _yaml_files(CATALOG / "constellations"):
        wrapper, constellation = _single_wrapper(path)
        assert wrapper == "constellation", path
        _assert_exact_keys(
            constellation,
            {"id", "node", "orbit", "planes", "slots_per_plane", "phasing", "node_tags"},
            {"display_name", "tags", "reference", "notes"},
            path,
            "constellation",
        )
        _assert_identifier(constellation["id"], path, "constellation.id")
        _catalog_ref_path(constellation["node"], path, "node")
        _catalog_ref_path(constellation["orbit"], path, "orbit")
        planes = constellation["planes"]
        assert set(planes) == {"count", "raan_spacing_deg"}, (path, planes)
        _assert_positive_int(planes["count"], path, "planes.count")
        _assert_nonnegative_number(planes["raan_spacing_deg"], path, "planes.raan_spacing")
        _assert_positive_int(constellation["slots_per_plane"], path, "slots_per_plane")
        phasing = constellation["phasing"]
        _assert_exact_keys(phasing, {"mode"}, {"phase_offset_deg"}, path, "phasing")
        assert phasing["mode"] in PHASING_MODES, (path, phasing)
        if "phase_offset_deg" in phasing:
            _assert_finite_number(phasing["phase_offset_deg"], path, "phasing.phase_offset")
        assert isinstance(constellation["node_tags"], list), path
        for rule in constellation["node_tags"]:
            _assert_exact_keys(rule, {"tag"}, {"planes", "slots", "node_ids"}, path, "node_tags")
            _assert_identifier(rule["tag"], path, "node_tags.tag")


def test_space_node_sets_match_their_grammar() -> None:
    paths = _yaml_files(CATALOG / "space-node-sets")
    assert paths
    for path in paths:
        wrapper, node_set = _single_wrapper(path)
        assert wrapper == "space_node_set", path
        _assert_exact_keys(node_set, {"id", "nodes"}, {"tags"}, path, "space_node_set")
        _assert_identifier(node_set["id"], path, "space_node_set.id")
        node_ids: list[str] = []
        for node in node_set["nodes"]:
            _assert_exact_keys(
                node, {"id", "node"}, {"orbit", "state_vector", "tags", "clock"}, path, "space_node"
            )
            _assert_identifier(node["id"], path, "space_node.id")
            node_ids.append(node["id"])
            _catalog_ref_path(node["node"], path, "node")
            assert ("orbit" in node) ^ ("state_vector" in node), (path, node)
            if "orbit" in node:
                _assert_orbit(node["orbit"], path, f"{node['id']}.orbit")
            if "tags" in node:
                _assert_tags(node["tags"], path, f"{node['id']}.tags")
        _assert_unique(node_ids, path, "space node ids")


def test_sessions_use_segment_grammar_and_resolve_catalog_entries() -> None:
    paths = _session_files()
    assert paths
    for path in paths:
        session = _load(path)
        _assert_exact_keys(
            session,
            {"session", "segments"},
            {
                "link_rules",
                "addressing",
                "routing",
                "simulation",
                "time",
                "ephemeris",
                "orbit",
                "dispatch",
            },
            path,
            "session",
        )
        meta = session["session"]
        _assert_exact_keys(meta, {"name"}, {"display_name", "description"}, path, "session.meta")
        _assert_identifier(meta["name"], path, "session.name")
        segments = session["segments"]
        assert isinstance(segments, list) and segments, path
        segment_ids = [segment["id"] for segment in segments]
        _assert_unique(segment_ids, path, "segment ids")
        segment_set = set(segment_ids)
        for segment in segments:
            _assert_segment(segment, path)
        if "link_rules" in session:
            _assert_link_rules(session["link_rules"], path, segment_set)
        if "addressing" in session:
            _assert_addressing(session["addressing"], path, segment_set)
        if "routing" in session:
            link_rule_ids = {rule["id"] for rule in session.get("link_rules", [])}
            _assert_routing(session["routing"], path, segment_set, link_rule_ids)
        if "simulation" in session:
            candidate_limits = session["simulation"]["candidate_limits"]
            assert set(candidate_limits) == {"max_pairs_per_rule", "max_pairs_per_tick"}, (
                path,
                candidate_limits,
            )
            _assert_positive_int(candidate_limits["max_pairs_per_rule"], path, "max_pairs_per_rule")
            _assert_positive_int(candidate_limits["max_pairs_per_tick"], path, "max_pairs_per_tick")


def test_access_link_rules_have_terminal_range_for_their_declared_masks() -> None:
    for path in _session_files():
        session = _load(path)
        segments = {segment["id"]: segment for segment in session["segments"]}

        for rule in session.get("link_rules", []):
            endpoints = rule["endpoints"]
            endpoint_segments = [
                _selector_segments(endpoint["select"])
                for endpoint in endpoints
                if _terminal_selector_has_role(endpoint["terminal"], "access")
            ]
            if len(endpoint_segments) != 2:
                continue

            endpoint_kinds = [
                {
                    "space" if "source" in segments[segment_id] else "ground"
                    for segment_id in selected_segments
                }
                for selected_segments in endpoint_segments
            ]
            if {frozenset(kinds) for kinds in endpoint_kinds} != {
                frozenset({"ground"}),
                frozenset({"space"}),
            }:
                continue

            ground_endpoint = endpoints[endpoint_kinds.index({"ground"})]
            space_endpoint = endpoints[endpoint_kinds.index({"space"})]
            assert "min_elevation_deg" in ground_endpoint, (path, rule["id"], ground_endpoint)
            _assert_finite_number(
                ground_endpoint["min_elevation_deg"],
                path,
                f"{rule['id']}.min_elevation_deg",
            )

            ground_ranges = _selected_ground_access_ranges(ground_endpoint, segments, path)
            space_ranges = _selected_space_access_ranges(space_endpoint, segments, path)
            assert ground_ranges, (path, rule["id"], "no selected ground access terminals")
            assert space_ranges, (path, rule["id"], "no selected space access terminals")

            for space_range in space_ranges:
                required_range_km = _slant_range_at_elevation_km(
                    space_range["body_radius_km"],
                    space_range["max_altitude_km"],
                    ground_endpoint["min_elevation_deg"],
                )
                assert space_range["max_range_km"] >= required_range_km, (
                    path,
                    rule["id"],
                    space_range["label"],
                    "space terminal range cannot meet declared access mask",
                    round(space_range["max_range_km"], 3),
                    round(required_range_km, 3),
                )
                for ground_range in ground_ranges:
                    effective_mask_deg = max(
                        ground_endpoint["min_elevation_deg"],
                        ground_range["min_elevation_deg"],
                    )
                    required_range_km = _slant_range_at_elevation_km(
                        space_range["body_radius_km"],
                        space_range["max_altitude_km"],
                        effective_mask_deg,
                    )
                    assert ground_range["max_range_km"] >= required_range_km, (
                        path,
                        rule["id"],
                        ground_range["label"],
                        "ground install range cannot meet declared access mask",
                        round(ground_range["max_range_km"], 3),
                        round(required_range_km, 3),
                        effective_mask_deg,
                    )


def _assert_segment(segment: dict[str, Any], path: Path) -> None:
    _assert_identifier(segment["id"], path, "segment.id")
    if "tags" in segment:
        _assert_tags(segment["tags"], path, "segment.tags")
    assert "namespace" not in segment, (path, segment)
    assert "kind" not in segment, (path, segment)
    if "source" in segment:
        _assert_exact_keys(
            segment, {"id", "source"}, {"display_name", "tags", "clock"}, path, "space_segment"
        )
        source_path = _catalog_ref_path(segment["source"], path)
        assert _single_wrapper(source_path)[0] in {
            "constellation",
            "space_node",
            "space_node_set",
        }, (
            path,
            segment,
        )
    else:
        _assert_exact_keys(
            segment,
            {"id", "placement"},
            {"display_name", "tags", "clock", "apply", "overrides"},
            path,
            "ground_segment",
        )
        placement = segment["placement"]
        assert set(placement) == {"from_site_set"}, (path, segment)
        _catalog_ref_path(placement["from_site_set"], path, "site_set")
        if "apply" in segment:
            _assert_ground_apply(segment["apply"], path, "segment.apply")
        for override in segment.get("overrides", []):
            _assert_exact_keys(
                override,
                {"match"},
                {"tags", "scheduling", "originated_prefixes"},
                path,
                "ground_override",
            )
            assert set(override["match"]) == {"site"}, (path, override)
            _assert_identifier(override["match"]["site"], path, "ground_override.match.site")


def _assert_ground_apply(value: dict[str, Any], path: Path, label: str) -> None:
    _assert_exact_keys(
        value,
        set(),
        {"scheduling", "originated_prefixes", "tags"},
        path,
        label,
    )
    if "scheduling" in value:
        _assert_scheduling(value["scheduling"], path, f"{label}.scheduling")
    if "originated_prefixes" in value:
        assert isinstance(value["originated_prefixes"], dict), (path, label, value)
        for prefixes in value["originated_prefixes"].values():
            assert isinstance(prefixes, list) and prefixes, (path, label, value)
            for prefix in prefixes:
                ipaddress.ip_network(prefix, strict=False)
    if "tags" in value:
        _assert_tags(value["tags"], path, f"{label}.tags")


def _assert_link_rules(link_rules: Any, path: Path, segment_ids: set[str]) -> None:
    assert isinstance(link_rules, list), path
    ids: list[str] = []
    for rule in link_rules:
        _assert_exact_keys(
            rule,
            {"id", "endpoints", "topology"},
            {"enabled", "constraints", "class", "tags"},
            path,
            "link_rule",
        )
        _assert_identifier(rule["id"], path, "link_rule.id")
        ids.append(rule["id"])
        assert isinstance(rule["endpoints"], list) and len(rule["endpoints"]) == 2, (path, rule)
        for endpoint in rule["endpoints"]:
            _assert_exact_keys(
                endpoint, {"select", "terminal"}, {"min_elevation_deg"}, path, "endpoint"
            )
            _assert_node_selector(endpoint["select"], path, "endpoint.select", segment_ids)
            _assert_terminal_selector(endpoint["terminal"], path, "endpoint.terminal")
        topology = rule["topology"]
        assert topology["mode"] in TOPOLOGY_MODES, (path, topology)
        if topology["mode"] == "nearest_n":
            assert set(topology) == {"mode", "n"}, (path, topology)
            _assert_positive_int(topology["n"], path, "topology.n")
        elif topology["mode"] in {"visible_candidates", "nearest_visible"}:
            assert set(topology) == {"mode"}, (path, topology)
        else:
            assert set(topology) == {"mode", "pairs"}, (path, topology)
    _assert_unique(ids, path, "link rule ids")


def _assert_addressing(value: dict[str, Any], path: Path, segment_ids: set[str]) -> None:
    _assert_exact_keys(
        value,
        set(),
        {"loopbacks", "point_to_point", "terrestrial_prefixes"},
        path,
        "addressing",
    )
    for group, assignments in value.items():
        assert isinstance(assignments, list), (path, group, assignments)
        ids: list[str] = []
        for assignment in assignments:
            _assert_exact_keys(
                assignment,
                {"id", "applies_to"},
                {"ipv4_pool", "ipv6_pool", "prefix_length", "allocation"},
                path,
                f"addressing.{group}",
            )
            _assert_identifier(assignment["id"], path, "addressing.id")
            ids.append(assignment["id"])
            _assert_node_selector(
                assignment["applies_to"], path, "addressing.applies_to", segment_ids
            )
            assert "ipv4_pool" in assignment or "ipv6_pool" in assignment, (path, assignment)
            if "ipv4_pool" in assignment:
                _cidr(assignment["ipv4_pool"], path, "addressing.ipv4_pool")
            if "ipv6_pool" in assignment:
                _cidr(assignment["ipv6_pool"], path, "addressing.ipv6_pool")
            if "prefix_length" in assignment:
                _assert_positive_int(assignment["prefix_length"], path, "prefix_length")
            if "allocation" in assignment:
                assert assignment["allocation"] in ALLOCATION_MODES, (path, assignment)
        _assert_unique(ids, path, f"addressing.{group}.ids")


def _assert_routing(
    value: dict[str, Any],
    path: Path,
    segment_ids: set[str],
    link_rule_ids: set[str],
) -> None:
    _assert_exact_keys(value, {"domains"}, {"boundaries"}, path, "routing")
    domain_ids: list[str] = []
    for domain in value["domains"]:
        _assert_exact_keys(
            domain,
            {"id", "protocol", "selectors"},
            {"capabilities", "area_assignment", "timers"},
            path,
            "routing.domain",
        )
        _assert_identifier(domain["id"], path, "routing.domain.id")
        domain_ids.append(domain["id"])
        assert domain["protocol"] in ROUTING_PROTOCOLS, (path, domain)
        assert isinstance(domain["selectors"], list) and domain["selectors"], (path, domain)
        for selector in domain["selectors"]:
            _assert_node_selector(selector, path, "routing.domain.selector", segment_ids)
    _assert_unique(domain_ids, path, "routing.domain ids")
    domain_set = set(domain_ids)

    for boundary in value.get("boundaries", []):
        _assert_exact_keys(boundary, {"over", "adapter", "export"}, set(), path, "routing.boundary")
        assert boundary["over"] in link_rule_ids, (path, boundary, link_rule_ids)
        assert boundary["adapter"] in ROUTING_ADAPTERS, (path, boundary)
        for export in boundary["export"]:
            _assert_exact_keys(
                export,
                {"from", "to", "prefixes"},
                {"export_node_loopbacks", "install_via"},
                path,
                "routing.export",
            )
            assert export["from"] in domain_set, (path, export)
            assert export["to"] in domain_set, (path, export)
            assert export["from"] != export["to"], (path, export)
            prefixes = export["prefixes"]
            if isinstance(prefixes, list):
                for prefix in prefixes:
                    ipaddress.ip_network(prefix, strict=False)
            else:
                assert set(prefixes) == {"aggregate_of"}, (path, prefixes)
                assert prefixes["aggregate_of"] in AGGREGATE_SOURCES, (path, prefixes)


def _slant_range_at_elevation_km(
    body_radius_km: float, altitude_km: float, elevation_deg: float
) -> float:
    elevation_rad = math.radians(elevation_deg)
    orbital_radius_km = body_radius_km + altitude_km
    return math.sqrt(orbital_radius_km**2 - (body_radius_km * math.cos(elevation_rad)) ** 2) - (
        body_radius_km * math.sin(elevation_rad)
    )


def _orbit_max_altitude_km(orbit: dict[str, Any], owner: Path) -> tuple[float, float]:
    body = _body_by_ref(orbit["central_body"], owner)
    body_radius_km = body["mean_radius_km"]
    if "shape" in orbit:
        shape = orbit["shape"]
        if "altitude_km" in shape:
            return shape["altitude_km"], body_radius_km
        return shape["apogee_altitude_km"], body_radius_km
    elements = orbit["elements"]
    apogee_radius_km = elements["semi_major_axis_km"] * (1 + elements["eccentricity"])
    return apogee_radius_km - body_radius_km, body_radius_km


def _terminal_mount_matches(mount: dict[str, Any], selector: dict[str, Any], owner: Path) -> bool:
    terminal = _terminal_by_ref(mount["terminal"], owner)
    key = next(iter(selector))
    operand = selector[key]
    if key == "all":
        return all(_terminal_mount_matches(mount, child, owner) for child in operand)
    if key == "any":
        return any(_terminal_mount_matches(mount, child, owner) for child in operand)
    if key == "not":
        return not _terminal_mount_matches(mount, operand, owner)
    if key == "role":
        return mount["role"] == operand
    if key == "medium":
        return terminal["medium"] == operand
    if key == "mount":
        return mount["id"] == operand
    raise AssertionError((owner, "unknown terminal selector", selector))


def _terminal_selector_has_role(selector: dict[str, Any], role: str) -> bool:
    key = next(iter(selector))
    operand = selector[key]
    if key in {"all", "any"}:
        return any(_terminal_selector_has_role(child, role) for child in operand)
    if key == "not":
        return False
    return key == "role" and operand == role


def _selector_segments(selector: dict[str, Any]) -> set[str]:
    key = next(iter(selector))
    operand = selector[key]
    if key in {"all", "any"}:
        segments: set[str] = set()
        for child in operand:
            segments |= _selector_segments(child)
        return segments
    if key == "not":
        return set()
    if key == "segment":
        return {operand}
    return set()


def _node_selector_matches(
    selector: dict[str, Any],
    *,
    segment_id: str,
    tags: set[str],
    local_node_id: str,
    plane: int | None = None,
    slot: int | None = None,
) -> bool:
    key = next(iter(selector))
    operand = selector[key]
    if key == "all":
        return all(
            _node_selector_matches(
                child,
                segment_id=segment_id,
                tags=tags,
                local_node_id=local_node_id,
                plane=plane,
                slot=slot,
            )
            for child in operand
        )
    if key == "any":
        return any(
            _node_selector_matches(
                child,
                segment_id=segment_id,
                tags=tags,
                local_node_id=local_node_id,
                plane=plane,
                slot=slot,
            )
            for child in operand
        )
    if key == "not":
        return not _node_selector_matches(
            operand,
            segment_id=segment_id,
            tags=tags,
            local_node_id=local_node_id,
            plane=plane,
            slot=slot,
        )
    if key == "segment":
        return segment_id == operand
    if key == "tag":
        return operand in tags
    if key == "node":
        return local_node_id == operand
    if key == "plane":
        return plane == operand
    if key == "slot":
        return slot == operand
    raise AssertionError(("unknown node selector", selector))


def _selected_ground_access_ranges(
    endpoint: dict[str, Any],
    segments: dict[str, dict[str, Any]],
    owner: Path,
) -> list[dict[str, Any]]:
    selector = endpoint["select"]
    terminal_selector = endpoint["terminal"]
    ranges: list[dict[str, Any]] = []
    for segment_id in _selector_segments(selector):
        segment = segments[segment_id]
        site_set = _site_set_by_ref(segment["placement"]["from_site_set"], owner)
        applied_tags = set(segment.get("apply", {}).get("tags", []))
        for site_ref in site_set["sites"]:
            site = _site_by_ref(site_ref, owner)
            for site_node in site["nodes"]:
                node_tags = set(site_node.get("tags", [])) | applied_tags
                if not _node_selector_matches(
                    selector,
                    segment_id=segment_id,
                    tags=node_tags,
                    local_node_id=site_node["id"],
                ):
                    continue
                node_model = _node_by_ref(site_node["model"], owner)
                model_mounts = {mount["id"]: mount for mount in node_model["terminals"]}
                for mount_id, installation in site_node["terminals"].items():
                    mount = model_mounts[mount_id]
                    if not _terminal_mount_matches(mount, terminal_selector, owner):
                        continue
                    terminal = _terminal_by_ref(mount["terminal"], owner)
                    capabilities = installation.get("capabilities", {})
                    limits = capabilities.get("limits", terminal["limits"])
                    ranges.append(
                        {
                            "label": f"{site['id']}/{site_node['id']}/{mount_id}",
                            "max_range_km": capabilities.get(
                                "max_range_km",
                                terminal["max_range_km"],
                            ),
                            "min_elevation_deg": limits["elevation_deg"]["min"],
                        }
                    )
    return ranges


def _selected_space_access_ranges(
    endpoint: dict[str, Any],
    segments: dict[str, dict[str, Any]],
    owner: Path,
) -> list[dict[str, Any]]:
    selector = endpoint["select"]
    terminal_selector = endpoint["terminal"]
    ranges: list[dict[str, Any]] = []
    for segment_id in _selector_segments(selector):
        segment = segments[segment_id]
        source_path = _catalog_ref_path(segment["source"], owner)
        wrapper, source = _single_wrapper(source_path)
        for record in _space_records_from_source(wrapper, source, owner):
            tags = set(record.get("tags", [])) | set(segment.get("tags", []))
            if not _node_selector_matches(
                selector,
                segment_id=segment_id,
                tags=tags,
                local_node_id=record["id"],
                plane=record.get("plane"),
                slot=record.get("slot"),
            ):
                continue
            max_altitude_km, body_radius_km = _orbit_max_altitude_km(record["orbit"], owner)
            for mount in record["node"]["terminals"]:
                if not _terminal_mount_matches(mount, terminal_selector, owner):
                    continue
                terminal = _terminal_by_ref(mount["terminal"], owner)
                ranges.append(
                    {
                        "label": f"{segment_id}/{record['id']}/{mount['id']}",
                        "max_range_km": terminal["max_range_km"],
                        "max_altitude_km": max_altitude_km,
                        "body_radius_km": body_radius_km,
                    }
                )
    return ranges


def _space_records_from_source(
    wrapper: str,
    source: dict[str, Any],
    owner: Path,
) -> list[dict[str, Any]]:
    if wrapper == "constellation":
        orbit_path = _catalog_ref_path(source["orbit"], owner, "orbit")
        return [
            {
                "id": source["id"],
                "node": _node_by_ref(source["node"], owner),
                "orbit": _load(orbit_path)["orbit"],
                "tags": source.get("tags", []),
            }
        ]
    if wrapper == "space_node_set":
        records = []
        for node in source["nodes"]:
            if "orbit" not in node:
                continue
            records.append(
                {
                    "id": node["id"],
                    "node": _node_by_ref(node["node"], owner),
                    "orbit": node["orbit"],
                    "tags": node.get("tags", []) + source.get("tags", []),
                }
            )
        return records
    if wrapper == "space_node":
        if "orbit" not in source:
            return []
        return [
            {
                "id": source["id"],
                "node": _node_by_ref(source["node"], owner),
                "orbit": source["orbit"],
                "tags": source.get("tags", []),
            }
        ]
    return []
