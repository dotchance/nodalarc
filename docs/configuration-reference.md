# Configuration Reference

NodalArc sessions are configured through YAML files that describe what you want to emulate: which constellation, which ground stations, and which routing protocol. The easiest way to create a session is through the wizard in the web UI, but you can also write the YAML by hand or modify existing examples.

## Creating a Session from the UI

The session wizard at http://localhost:3000 walks you through the configuration:

1. **Choose a constellation** - Starlink, OneWeb, Kuiper, or a custom geometry
2. **Choose a satellite type** - defines ISL terminal count, range, and bandwidth
3. **Choose ground stations** - a predefined set (global, US, etc.) or pick individual stations
4. **Choose a routing protocol** - IS-IS, OSPF, or NodalPath, with optional extensions
5. **Choose an area strategy** - how to divide the constellation into routing areas
6. **Deploy** - the wizard generates the session YAML and deploys it

![Session Wizard](images/vf-session-wizard.png)
<!-- TODO: Screenshot showing the session wizard -->

Behind the scenes, the wizard calls the VS-API which generates a session YAML file and applies it as a ConstellationSpec to the cluster. Everything you can do in the wizard, you can also do from the command line.

## Building Blocks

NodalArc configurations are modular. Each component is defined independently, then assembled in a session config. Think of them as lego blocks:

- **Satellite type** - the hardware on each satellite (ISL laser terminals, ground-facing antennas)
- **Constellation** - orbital geometry (altitude, planes, spacing) + which satellite type to use
- **Ground stations** - locations, tracking antennas, terrestrial network connections
- **Routing protocol** - IS-IS, OSPF, or NodalPath, with extensions like SR-MPLS

You mix and match to create different experiments. Pair a Starlink constellation with Iridium ground stations. Run IS-IS on a topology designed for OSPF. Put optical ground terminals under RF satellites. Not every combination produces a working network. That's the point. You experiment to see what works.

```
Satellite Type          Constellation         Ground Station Set        Routing
(what the sat carries)  (orbital geometry)     (what's on the ground)    (protocol config)
        │                      │                       │                      │
        └──────────┬───────────┘                       │                      │
                   │                                   │                      │
              Session Config ──────────────────────────┘──────────────────────┘
```

## Session Configuration

A session config is the top-level file that assembles the building blocks. Here's a real example:

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

time:
  step_seconds: 1
```

That's it. This deploys 176 satellites across 16 orbital planes with 7 ground stations, running IS-IS with traffic engineering extensions, each orbital plane as its own routing area.

### Session Fields

| Field | Required | Description |
|-------|----------|-------------|
| `session.name` | yes | Session identifier |
| `constellation` | yes | Path to constellation config (or inline YAML) |
| `ground_stations` | yes | Path to ground station set (or inline YAML) |
| `routing.protocol` | yes | Routing protocol: `isis`, `ospf`, or `nodalpath` |
| `routing.extensions` | no | Protocol extensions (see below) |
| `routing.area_assignment.strategy` | no | Area strategy: `flat`, `per-plane`, `stripe` (default: `flat`) |
| `routing.area_assignment.planes_per_stripe` | no | Planes per area (required for `stripe` strategy) |
| `routing.config_overrides` | no | Key-value overrides passed to FRR config templates |
| `placement.policy` | no | Pod placement: `allOnOne`, `planePerNode`, `planeGroupPerNode` |
| `time.step_seconds` | no | Simulation time step in seconds (default: 1) |
| `satellite_type` | no | Override the constellation's satellite type |

### Routing Protocols and Extensions

NodalArc supports three routing protocols, each with optional extensions:

| Protocol | What it is | Available Extensions |
|----------|-----------|---------------------|
| `isis` | IS-IS, link-state IGP widely used in satellite networks | `traffic-engineering`, `sr` (segment routing), `mpls` |
| `ospf` | OSPF, link-state IGP common in enterprise networks | `traffic-engineering`, `sr`, `mpls` |
| `nodalpath` | Centralized path computation (NodalPath PCE engine) | none |

Extension dependencies:
- `mpls` requires `traffic-engineering`
- `sr` (segment routing) requires `isis` or `ospf`

Examples:

```yaml
# IS-IS with traffic engineering
routing:
  protocol: isis
  extensions:
    - traffic-engineering
```

```yaml
# OSPF with SR-MPLS (segment routing over MPLS)
routing:
  protocol: ospf
  extensions:
    - traffic-engineering
    - sr
    - mpls
```

```yaml
# Plain IS-IS with no extensions
routing:
  protocol: isis
```

### Area Assignment Strategies

For IS-IS and OSPF, the constellation is divided into routing areas. The strategy controls how:

| Strategy | How it works | When to use |
|----------|-------------|-------------|
| `flat` | All nodes in one area | Small constellations (<50 sats), simplest setup |
| `per-plane` | Each orbital plane is its own area | Recommended for large constellations. Limits flooding scope |
| `stripe` | Groups of N adjacent planes share an area | Balance between flooding scope and inter-area traffic |

```yaml
# One area per orbital plane (recommended)
routing:
  area_assignment:
    strategy: per-plane
```

```yaml
# Two planes per area
routing:
  area_assignment:
    strategy: stripe
    planes_per_stripe: 2
```

### Placement Policies (Multi-Node)

For clusters with multiple compute nodes, the placement policy controls how satellite pods are distributed:

| Policy | How it works |
|--------|-------------|
| `allOnOne` | All pods on one node (default for single-node clusters) |
| `planePerNode` | Each orbital plane on a separate node. Intra-plane links are fast local connections, cross-plane links use tunnels |
| `planeGroupPerNode` | Groups of adjacent planes per node. Reduces tunnel count |

```yaml
placement:
  policy: planePerNode
```

## Constellation Configuration

Constellation configs define the orbital geometry: how many satellites, at what altitude, in what pattern.

### Parametric Mode

Auto-generate a Walker constellation from orbital parameters:

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
| `mode` | `parametric` for auto-generated constellations |
| `name` | Constellation identifier |
| `satellite_type` | Reference to a satellite type config (defines ISL terminals, range, bandwidth) |
| `orbit.altitude_km` | Orbital altitude in kilometers |
| `orbit.inclination_deg` | Orbital inclination in degrees |
| `orbit.pattern` | Orbital pattern: `walker-delta` or `walker-star` |
| `planes.count` | Number of orbital planes |
| `planes.sats_per_plane` | Satellites per plane |
| `planes.raan_spacing_deg` | Right Ascension spacing between planes |
| `planes.phase_offset_deg` | Phase offset between adjacent planes (Walker pattern parameter) |

### Explicit Mode

List individual satellites with custom orbital elements:

```yaml
mode: explicit
name: custom-4sat
satellites:
  - plane: 0
    slot: 0
    altitude_km: 550
    inclination_deg: 53.0
    raan_deg: 0.0
    true_anomaly_deg: 0.0
  - plane: 0
    slot: 1
    altitude_km: 550
    inclination_deg: 53.0
    raan_deg: 0.0
    true_anomaly_deg: 90.0
```

## Satellite Type Configuration

A satellite type defines the physical hardware on the satellite bus: the inter-satellite link (ISL) terminals that talk to other satellites, and the ground-facing antennas that talk to ground stations. These are satellite characteristics, not ground station characteristics.

```yaml
satellite_type:
  name: starlink-v2
  description: "Starlink Gen2 optical laser ISL platform"

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

**ISL terminals** - the laser or RF links between satellites:

| Field | Description |
|-------|-------------|
| `type` | `optical` (laser) or `rf` (radio frequency) |
| `count` | Number of terminals (Starlink V2 has 4 optical) |
| `max_range_km` | Maximum link range. Determines which satellites can connect |
| `bandwidth_mbps` | Link capacity per terminal |
| `max_tracking_rate_deg_s` | How fast the terminal can slew to track a moving peer |
| `field_of_regard_deg` | Angular range the terminal can point (360 = hemispherical) |
| `role` | Optional: `intra-plane`, `cross-plane`, or omit for a shared pool |

**Ground terminals** - the satellite's downlink antennas (what the satellite uses to talk to the ground):

| Field | Description |
|-------|-------------|
| `type` | `optical` or `rf` |
| `count` | Number of ground-facing antennas on the satellite |
| `bandwidth_mbps` | Downlink/uplink capacity per antenna |
| `band` | RF frequency band (e.g., `Ku`, `Ka`). RF terminals only |
| `beam_falloff_exponent` | Signal degradation model as elevation angle decreases |

Satellite types are defined in `configs/satellite-types/` and referenced by name from constellation configs. A constellation with `satellite_type: starlink-v2` means every satellite in that constellation carries the hardware defined in `starlink-v2.yaml`.

## Ground Station Configuration

Ground stations are the other end of the link. They define what's on the ground: geographic location, upward-tracking antennas, and the terrestrial network prefixes the station advertises into the routing protocol. Ground station characteristics are completely independent of satellite characteristics.

### Individual Stations

Each station defines its location, its tracking antennas (pointing up at satellites), and its terrestrial network connections:

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

**Location:**

| Field | Description |
|-------|-------------|
| `name` | Station identifier (becomes `gs-{name}` in the network) |
| `lat_deg`, `lon_deg` | WGS84 geographic coordinates |
| `alt_m` | Altitude above sea level in meters |
| `min_elevation_deg` | Minimum satellite elevation angle for visibility. Higher means fewer but higher-quality connections |

**Tracking terminals** - the ground station's antennas (what the ground station uses to talk to satellites):

| Field | Description |
|-------|-------------|
| `type` | `optical` or `rf`. Must be compatible with the satellite's ground terminal type |
| `count` | Number of tracking antennas at this station |
| `bandwidth_mbps` | Uplink/downlink capacity per antenna |
| `tracking_capacity` | Simultaneous satellite connections per antenna |

**Terrestrial prefixes** - the IP networks this ground station connects to on the terrestrial side:

| Field | Description |
|-------|-------------|
| `prefix` | IPv4 or IPv6 prefix advertised into the routing protocol |
| `metric` | Routing metric for this prefix |

The `0.0.0.0/0` prefix with metric 100 causes the ground station to originate a default route into the routing protocol. Satellites connected to this ground station will prefer the direct ground path for internet-bound traffic over routing through ISLs to a more distant ground station.

Individual stations live in `configs/ground-stations/stations/`.

### Station Sets

A set groups individual stations and provides default terrestrial prefix templates so you don't have to repeat them on every station:

```yaml
ground_station_set:
  name: global
  description: "7 stations across 6 continents"
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

The `{gs_index}` placeholder is replaced with the station's index (0-based) when generating per-station prefixes. When an individual station defines its own `terrestrial_prefixes`, those override the set defaults.

Station sets live in `configs/ground-stations/sets/`.

### How Satellite and Ground Station Terminals Interact

A ground link forms when a satellite's **ground terminal** can reach a ground station's **tracking terminal**. The link characteristics depend on both sides:

- The satellite's ground terminal type (optical/RF) must be compatible with the ground station's tracking terminal type
- The effective bandwidth is the minimum of the satellite's downlink capacity and the ground station's uplink capacity
- The ground station's `min_elevation_deg` determines when the satellite is high enough to establish a link
- The satellite's `beam_falloff_exponent` models signal degradation at low elevation angles

This separation means you can experiment: What happens if you put optical ground stations under a constellation with RF downlinks? The links won't form. What if you increase tracking capacity at a ground station from 1 to 3? It can connect to multiple satellites simultaneously. What if a satellite has only 1 ground terminal but is visible from five ground stations at once? Only one of those ground stations gets the link — the OME picks the best candidate by the configured scheduling policy (elevation angle, by default). Capacity is enforced on both sides: the ground station's tracking capacity and the satellite's `ground_terminal_count` each bound the number of simultaneous links. Each component is independently configurable.

## Putting It Together

A complete session deployment uses three config files:

```
Session Config (starlink-176-isis-te.yaml)
  ├── Constellation (starlink-176.yaml)
  │     └── Satellite Type (starlink-v2.yaml)
  └── Ground Station Set (global.yaml)
        ├── hawthorne.yaml
        ├── ashburn.yaml
        ├── frankfurt.yaml
        └── ... (7 stations)
```

The session config references a constellation and a ground station set. The constellation references a satellite type. The ground station set references individual station configs. All paths are relative to the project root.

## Deploying a Session

### From the UI

Use the session wizard. It generates the YAML and deploys automatically.

### From the Command Line

```bash
# Deploy the default session
sudo make session

# Deploy a specific session
sudo make session DEFAULT_SESSION=configs/sessions/starlink-176-isis-te.yaml
```

### Switching Sessions

To switch to a different session, tear down the current one first:

```bash
sudo make teardown
sudo make session DEFAULT_SESSION=configs/sessions/starlink-176-nodalpath.yaml
```

Or use the session wizard in the UI. It handles the teardown and redeploy automatically.

## Available Presets

### Constellations

| Config | Satellites | Planes | Altitude | Description |
|--------|-----------|--------|----------|-------------|
| `demo-36.yaml` | 36 | 1 | 550 km | Single orbital ring for demos (default) |
| `starlink-176.yaml` | 176 | 16 | 550 km | Starlink-scale Walker delta |
| `custom-example.yaml` | 4 | 2 | 550 km | Minimal test constellation |

### Ground Station Sets

| Config | Stations | Coverage | Description |
|--------|----------|----------|-------------|
| `demo.yaml` | 6 | 5 continents | Hawthorne, Ashburn, Denver, Frankfurt, Singapore, Tokyo |
| `global.yaml` | 7 | 6 continents | Hawthorne, Ashburn, Frankfurt, Singapore, Sao Paulo, Sydney, McMurdo |

### Satellite Types

| Config | ISL Terminals | ISL Range | Description |
|--------|--------------|-----------|-------------|
| `starlink-v2.yaml` | 4 optical | 5,000 km | Starlink Gen2 laser ISL |
