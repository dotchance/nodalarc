# Configuration Reference

NodalArc sessions are configured through YAML files that define what to emulate: which constellation, which ground stations, and which routing protocol. Users can create sessions through the browser wizard, but as an operator you may need to write or modify session configs directly.

## Session Configuration

A session config assembles the building blocks — constellation, ground stations, routing protocol — into a deployable emulation:

```yaml
session:
  name: starlink-176-isis-te

constellation: configs/constellations/starlink-176.yaml
ground_stations: configs/ground-stations/sets/global.yaml

routing:
  protocol: isis
  extensions:
    - traffic-engineering
  area_assignment:
    strategy: per-plane

placement:
  policy: planePerNode

time:
  step_seconds: 1
```

### Session Fields

| Field | Required | Default | Description |
|-------|:---:|---------|-------------|
| `session.name` | yes | — | Session identifier |
| `constellation` | yes | — | Path to constellation YAML |
| `ground_stations` | yes | — | Path to ground station set YAML |
| `routing.protocol` | yes | — | `isis`, `ospf`, or `nodalpath` |
| `routing.extensions` | no | none | Protocol extensions (see below) |
| `routing.area_assignment.strategy` | no | `flat` | `flat`, `per-plane`, or `stripe` |
| `routing.area_assignment.planes_per_stripe` | no | — | Required for `stripe` strategy |
| `routing.config_overrides` | no | none | Key-value overrides for FRR templates |
| `placement.policy` | no | `allOnOne` | `allOnOne`, `planePerNode`, `planeGroupPerNode` |
| `time.step_seconds` | no | 1 | Simulation time step in seconds |
| `satellite_type` | no | from constellation | Override the constellation's satellite type |

### Routing Protocols

| Protocol | Extensions Available | Description |
|----------|---------------------|-------------|
| `isis` | `traffic-engineering`, `sr`, `mpls` | IS-IS link-state IGP |
| `ospf` | `traffic-engineering`, `sr`, `mpls` | OSPF link-state IGP |
| `nodalpath` | none | Centralized path computation |

Extension dependencies:
- `mpls` requires `traffic-engineering`
- `sr` (segment routing) requires `isis` or `ospf`

### Area Strategies

| Strategy | Description | Recommended For |
|----------|-------------|-----------------|
| `flat` | All nodes in one routing area | Constellations < 50 satellites |
| `per-plane` | Each orbital plane is its own area | Large constellations (recommended) |
| `stripe` | N adjacent planes share an area | Balance between scope and inter-area traffic |

## Constellation Configuration

Defines orbital geometry. Located in `configs/constellations/`.

### Parametric Mode (Walker Constellation)

```yaml
mode: parametric
name: starlink-176
satellite_type: starlink-v2
orbit:
  altitude_km: 550
  inclination_deg: 53
  pattern: walker-delta
planes:
  count: 16
  raan_spacing_deg: 22.5
  sats_per_plane: 11
  phase_offset_deg: 2.045
```

| Field | Description |
|-------|-------------|
| `orbit.altitude_km` | Orbital altitude in km |
| `orbit.inclination_deg` | Orbital inclination in degrees |
| `orbit.pattern` | `walker-delta` or `walker-star` |
| `planes.count` | Number of orbital planes |
| `planes.sats_per_plane` | Satellites per plane |
| `planes.raan_spacing_deg` | Right Ascension spacing between planes |
| `planes.phase_offset_deg` | Phase offset between adjacent planes |

### Explicit Mode (Custom Orbits)

```yaml
mode: explicit
name: custom-constellation
satellites:
  - plane: 0
    slot: 0
    altitude_km: 550
    inclination_deg: 53.0
    raan_deg: 0.0
    true_anomaly_deg: 0.0
```

### Available Constellations

| Config | Satellites | Planes | Altitude | Pattern |
|--------|-----------|--------|----------|---------|
| `demo-36.yaml` | 36 | 1 | 550 km | Single ring |
| `starlink-176.yaml` | 176 | 16 | 550 km | Walker delta |
| `starlink-576.yaml` | 576 | 36 | 550 km | Walker delta |
| `starlink-shell1-220.yaml` | 220 | 20 | 550 km | Walker delta |
| `starlink-gen2-1584.yaml` | 1584 | 72 | 530 km | Walker delta |
| `iridium-66.yaml` | 66 | 6 | 780 km | Walker star |
| `iridium-small-36.yaml` | 36 | 6 | 780 km | Walker star (reduced) |
| `oneweb-60.yaml` | 60 | 6 | 1200 km | Walker delta |
| `kuiper-50.yaml` | 50 | 5 | 630 km | Walker delta |
| `custom-example.yaml` | 4 | 2 | 550 km | Minimal test |

## Satellite Type Configuration

Defines the hardware on each satellite bus. Located in `configs/satellite-types/`.

```yaml
satellite_type:
  name: starlink-v2
  isl_terminals:
    - type: optical
      count: 4
      max_range_km: 5000
      bandwidth_mbps: 100
      max_tracking_rate_deg_s: 3.0
      field_of_regard_deg: 140
  ground_terminals:
    - type: rf
      count: 1
      bandwidth_mbps: 1000
      band: Ku
      beam_falloff_exponent: 2.0
```

**ISL terminals** — laser or RF links between satellites:

| Field | Description |
|-------|-------------|
| `type` | `optical` or `rf` |
| `count` | Number of ISL terminals (determines max simultaneous ISLs) |
| `max_range_km` | Maximum link range |
| `bandwidth_mbps` | Per-terminal capacity |
| `max_tracking_rate_deg_s` | Terminal slew rate |
| `field_of_regard_deg` | Pointing range (360 = hemispherical) |

**Ground terminals** — satellite's downlink antennas:

| Field | Description |
|-------|-------------|
| `type` | `optical` or `rf` |
| `count` | Number of ground-facing antennas (limits simultaneous ground links) |
| `bandwidth_mbps` | Downlink capacity per antenna |
| `band` | RF frequency band (RF terminals only) |
| `beam_falloff_exponent` | Signal degradation at low elevation |

### Available Satellite Types

| Config | ISL Count | ISL Type | ISL Range | Description |
|--------|-----------|----------|-----------|-------------|
| `starlink-v2.yaml` | 4 | optical | 5,000 km | Starlink Gen2 |
| `starlink-v2-laser.yaml` | 4 | optical | 5,000 km | Laser-only variant |
| `generic-4isl.yaml` | 4 | optical | 5,000 km | Generic platform |
| `generic-2isl.yaml` | 2 | optical | 5,000 km | Intra-plane only |
| `iridium-next.yaml` | 4 | RF | 4,400 km | Iridium NEXT |
| `kuiper-v1.yaml` | 4 | optical | 5,000 km | Amazon Kuiper |
| `oneweb-gen2.yaml` | 4 | optical | 5,000 km | OneWeb Gen2 |

## Ground Station Configuration

Located in `configs/ground-stations/`.

### Individual Stations (`stations/`)

```yaml
ground_station:
  name: hawthorne
  lat_deg: 33.92
  lon_deg: -118.33
  alt_m: 20
  min_elevation_deg: 15
  terminals:
    - type: optical
      count: 2
      bandwidth_mbps: 1000
      tracking_capacity: 1
  terrestrial_prefixes:
    - prefix: "172.16.1.0/24"
      metric: 10
    - prefix: "0.0.0.0/0"
      metric: 100
```

| Field | Description |
|-------|-------------|
| `name` | Identifier (becomes `gs-{name}` in the network) |
| `lat_deg`, `lon_deg` | WGS84 coordinates |
| `alt_m` | Altitude above sea level (meters) |
| `min_elevation_deg` | Minimum satellite elevation for link formation |
| `terminals[].tracking_capacity` | Simultaneous satellite connections per antenna |
| `terrestrial_prefixes` | IP prefixes advertised into the routing protocol |

### Station Sets (`sets/`)

```yaml
ground_station_set:
  name: global
  stations:
    - hawthorne
    - ashburn
    - frankfurt
    - singapore
    - sao-paulo
    - sydney
    - mcmurdo
  default_terrestrial_prefixes:
    ipv4_template: "172.16.{gs_index}.0/24"
    ipv6_template: "fd10::{gs_index}:0/112"
    metric: 10
    default_route: true
    default_route_metric: 100
```

The `{gs_index}` template is replaced with each station's 0-based index. Individual station configs override set defaults when both define `terrestrial_prefixes`.

### Available Ground Station Sets

| Config | Stations | Coverage |
|--------|----------|----------|
| `demo.yaml` | 6 | US + Europe + Asia |
| `global.yaml` | 7 | 6 continents |
| `global-8.yaml` | 8 | 6 continents + polar |
| `us-conus.yaml` | 4 | Continental US |
| `transatlantic.yaml` | 4 | US East + Europe |
| `transpacific.yaml` | 4 | US West + Asia-Pacific |
| `polar-emphasis.yaml` | 6 | High-latitude stations |

## Config File Structure

```
configs/
├── constellations/         Orbital geometry definitions
├── satellite-types/        Satellite hardware definitions
├── ground-stations/
│   ├── stations/           Individual ground station configs
│   └── sets/               Named groups of stations
├── sessions/               Complete session configs (references above)
├── templates/frr/          Jinja2 templates for FRR config generation
├── presets/                 UI wizard preset metadata
└── platform.yaml           Platform-level settings
```

Session configs reference constellations and ground station sets by path. Paths are relative to the project root.
