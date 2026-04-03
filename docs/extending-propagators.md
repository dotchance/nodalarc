# Extending Propagators

How to replace or extend the Keplerian orbital propagator in Nodal Arc.

## Current Propagator

The default propagator is at `ome/propagator.py`. Pure math, no I/O, under 300 lines. It implements circular Keplerian orbits (eccentricity = 0), which is sufficient for LEO constellation emulation where precise orbital perturbations are not required.

## Public API

The primary entry point used by OME:

```python
def propagate_keplerian(
    elements: OrbitalElements,
    epoch_unix: float,
    dt: float,
) -> tuple[Vec3, Vec3, GeoPosition]:
    """Propagate and return (ecef_position_km, ecef_velocity_km_s, geodetic_position)."""
```

### Supporting Functions

| Function | Signature | Description |
|----------|-----------|-------------|
| `orbital_period` | `(altitude_km) -> float` | Orbital period in seconds (T = 2pi sqrt(a^3/mu)) |
| `orbital_velocity` | `(altitude_km) -> float` | Orbital velocity in km/s (v = sqrt(mu/a)) |
| `elements_from_params` | `(altitude_km, inclination_deg, raan_deg, true_anomaly_deg) -> OrbitalElements` | Convert human-readable params to OrbitalElements |
| `propagate_eci` | `(elements, dt) -> (Vec3, Vec3)` | Propagate in ECI frame, returns (position, velocity) |
| `gmst` | `(unix_timestamp) -> float` | Greenwich Mean Sidereal Time in radians |
| `eci_to_ecef` | `(pos_eci, unix_timestamp) -> Vec3` | ECI to ECEF coordinate rotation |
| `ecef_to_geodetic` | `(pos_ecef) -> GeoPosition` | ECEF (km) to lat/lon/alt on WGS84 ellipsoid |
| `geodetic_to_ecef` | `(pos) -> Vec3` | Geodetic to ECEF conversion |
| `distance_km` | `(a, b) -> float` | Euclidean distance between two Vec3 points |

## Data Types

All types are `NamedTuple` for immutability and tuple-unpacking convenience.

```python
class Vec3(NamedTuple):
    x: float
    y: float
    z: float

class OrbitalElements(NamedTuple):
    semi_major_axis_km: float  # a = altitude + Earth radius
    inclination_rad: float     # i
    raan_rad: float            # RAAN (Omega)
    true_anomaly_rad: float    # nu at epoch

class GeoPosition(NamedTuple):
    lat_deg: float
    lon_deg: float
    alt_km: float
```

## Coordinate Frames

The propagator works with three coordinate frames:

1. **ECI (Earth-Centered Inertial)** - Fixed to the stars. Propagation math happens here. The perifocal-to-ECI rotation uses RAAN and inclination.

2. **ECEF (Earth-Centered Earth-Fixed)** - Rotates with the Earth. Conversion from ECI uses GMST (Greenwich Mean Sidereal Time) rotation about the Z axis.

3. **Geodetic** - Latitude, longitude, altitude on the WGS84 ellipsoid. Conversion from ECEF uses the iterative Bowring method.

The propagation pipeline:
```
OrbitalElements + dt
    -> propagate_eci()      -> ECI position + velocity
    -> eci_to_ecef()        -> ECEF position
    -> ecef_to_geodetic()   -> lat/lon/alt
```

## How to Add a New Propagator

### Option 1: SGP4/SDP4 (TLE-based)

For higher fidelity using Two-Line Element sets:

1. Create `ome/propagator_sgp4.py`
2. Accept TLE strings and convert to internal representation (use the `sgp4` Python package)
3. Implement the same function signature:
   ```python
   def propagate_sgp4(
       tle_line1: str,
       tle_line2: str,
       epoch_unix: float,
       dt: float,
   ) -> tuple[Vec3, Vec3, GeoPosition]:
   ```
4. The sgp4 library returns ECI position/velocity directly. Use the existing `eci_to_ecef()` and `ecef_to_geodetic()` for coordinate conversion
5. Wire in via the constellation loader: in `ome/main.py`, import your propagator for TLE-mode constellations

### Option 2: J2 Perturbation

For secular drift from Earth's oblateness without full SGP4:

1. Extend `propagate_eci()` with J2 secular drift terms on RAAN and argument of perigee:
   ```
   dOmega/dt = -1.5 * n * J2 * (R_e/a)^2 * cos(i)
   domega/dt = 0.75 * n * J2 * (R_e/a)^2 * (5*cos^2(i) - 1)
   ```
   where J2 = 1.08263e-3
2. Apply these as linear corrections to RAAN and true anomaly in `propagate_eci()`
3. No API change needed. The same `propagate_keplerian()` signature works

### Option 3: Numerical Integration

For arbitrary force models (drag, solar radiation pressure, third-body effects):

1. Create `ome/propagator_numerical.py`
2. Use RK4 or similar integrator on the equations of motion with your force model
3. Return the same `(Vec3, Vec3, GeoPosition)` tuple
4. Performance note: numerical integration is significantly slower per step. Consider caching or pre-computing trajectories

## Wiring In

The constellation loader in `ome/main.py` creates `OrbitalElements` for each satellite and passes them to the propagator. To use a different propagator:

1. Change the import in `ome/main.py`:
   ```python
   # from ome.propagator import propagate_keplerian
   from ome.propagator_sgp4 import propagate_sgp4 as propagate_keplerian
   ```

2. Or select at runtime based on constellation mode:
   ```python
   if constellation.mode == "tle":
       from ome.propagator_sgp4 import propagate_sgp4
       # use propagate_sgp4 for each satellite
   else:
       from ome.propagator import propagate_keplerian
       # use propagate_keplerian for parametric/explicit
   ```

## Testing

Verify your propagator with these checks:

1. **Orbital period test:** Propagate a satellite for exactly one orbital period. It should return to within 1 km of its starting ECEF position (accounting for Earth rotation, the ECI position should be exact).

2. **Ground track test:** Propagate for several orbits and verify the ground track (lat/lon series) looks reasonable: ascending/descending crossings at the correct inclination, westward drift between orbits.

3. **Velocity consistency:** Verify `orbital_velocity(altitude)` matches the magnitude of the velocity vector returned by `propagate_eci()`.

4. **Known position test:** For SGP4, compare against published TLE propagation results from CelesTrak or Space-Track.
