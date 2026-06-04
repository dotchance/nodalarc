# Configuration

Runtime configuration files mounted into containers or read at deploy time.

## Directories

| Directory | Purpose |
|-----------|---------|
| `constellations/` | Full constellation definitions — orbital parameters, plane layout, satellite type. Referenced by session YAMLs. |
| `presets/constellations/` | Wizard metadata — display name, description, satellite count, plus reference to the actual constellation YAML and default ground stations. Used by the session creation wizard in the VF. |
| `ground-stations/stations/` | Individual ground station definitions (lat, lon, elevation). |
| `ground-stations/sets/` | Named sets of ground stations (e.g., `global-8.yaml` = 8 stations worldwide). |
| `satellite-types/` | Satellite hardware definitions — ISL terminal count, bandwidth, tracking rate. |
| `scenarios/` | Failure injection scenarios (link failure, satellite loss, compound). |
| `sessions/` | Curated segment-session YAMLs for manual deployment (e.g., `earth-leo-simple.yaml`). |
| `templates/frr/` | Jinja2 templates for FRR daemon configuration files. |
| `platform.yaml` | Platform-level settings (NATS URL, service ports, system tuning). |

## Constellations vs Presets

- `constellations/starlink-176.yaml` — the orbital mechanics definition (altitude, inclination, planes, phase offset)
- `presets/constellations/starlink-176.yaml` — wizard entry pointing to the above, plus display metadata

Session YAMLs assemble reusable building blocks through `segments` and `link_rules`:
```yaml
segments:
  - id: space
    kind: constellation
    source: configs/constellations/starlink-176.yaml
    namespace: space
    central_body: earth
  - id: ground
    kind: ground_set
    source: configs/ground-stations/sets/starlink-176.yaml
    namespace: ground
    reference_body: earth
link_rules:
  - id: ground-access
    kind: access
    endpoints:
      - selector: {segment: ground}
        terminal_role: ground
      - selector: {segment: space}
        terminal_role: ground
    topology: {mode: visible_candidates}
```

The wizard reads presets to populate the dropdown, then generates the same segment YAML accepted by upload/deploy.
