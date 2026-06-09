# Runtime Support Configuration

This directory contains platform support files that are mounted into containers
or read by deploy-time tooling.

Reusable NodalArc model data now lives under `catalog/nodalarc/`, and assembled
session examples live under `catalog/nodalarc/sessions/`.

## Directories

| Directory | Purpose |
|-----------|---------|
| `ephemerides/` | Local ephemeris kernels used by multi-body sessions. |
| `templates/frr/` | Jinja2 templates for generated FRR daemon configuration files. |
| `platform.yaml` | Platform-level settings such as NATS URL, service ports, and system tuning. |

The old constellation, satellite-type, ground-station, preset, scenario, and
session examples were removed during the catalog grammar reset. New examples
must be authored against the catalog/session grammar:

```yaml
segments:
  - id: leo
    source: nodalarc:constellations/earth/leo/earth-leo-simple-36.yaml

	  - id: ground
	    placement:
	      from_site_set: nodalarc:site-sets/earth/leo/starlink-demo-gateways.yaml
	    apply:
	      originated_prefixes:
	        ipv4: [0.0.0.0/0]

	link_rules:
	  - id: leo_access
	    topology: {mode: visible_candidates}
	    endpoints:
	      - select:
	          all:
	            - segment: ground
	            - tag: leo_gs
	        terminal:
	          all:
	            - role: access
	            - medium: rf
	        min_elevation_deg: 25
	      - select:
	          segment: leo
	        terminal:
	          all:
	            - role: access
	            - medium: rf
```
