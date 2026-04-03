# Extending the Orbital Propagator

NodalArc uses a Keplerian orbital propagator to compute satellite positions. It's pure math with no I/O, under 260 lines, and handles circular orbits (eccentricity = 0). This is sufficient for LEO constellation emulation where you care about topology dynamics, not precise orbit determination.

If you need higher fidelity (TLE-based propagation, J2 perturbations, drag modeling), the propagator is designed to be replaced.

## Where the Code Lives

| File | What it contains |
|------|-----------------|
| `services/ome/propagator.py` | The propagator: ECI propagation, coordinate conversions, the `propagate_keplerian()` entry point |
| `lib/nodalarc/orbital.py` | `OrbitalElements` and `elements_from_params()` data types |
| `lib/nodalarc/constants.py` | Physical constants: EARTH_MU, EARTH_RADIUS_KM, WGS84 ellipsoid parameters |
| `services/ome/event_stream.py` | The OME event stream, which calls `propagate_keplerian()` for every satellite at every time step |

## Public API

The OME calls one function per satellite per time step:

```python
def propagate_keplerian(
    elements: OrbitalElements,
    epoch_unix: float,
    dt: float,
) -> tuple[Vec3, Vec3, GeoPosition]:
    """
    Args:
        elements: Orbital elements at epoch (semi-major axis, inclination, RAAN, true anomaly)
        epoch_unix: Unix timestamp of the epoch
        dt: Seconds elapsed since epoch

    Returns:
        (ecef_position_km, ecef_velocity_km_s, geodetic_position)
    """
```

This is called in `services/ome/event_stream.py` line 68:

```python
ecef, vel_eci, geo = propagate_keplerian(sat.elements, epoch_unix, dt)
```

A replacement propagator must return the same 3-tuple. Everything downstream (visibility computation, latency calculation, position publishing) works with ECEF positions and geodetic coordinates.

## Supporting Functions

All in `services/ome/propagator.py`:

| Function | What it does |
|----------|-------------|
| `orbital_period(altitude_km)` | Orbital period in seconds. T = 2pi * sqrt(a^3/mu) |
| `orbital_velocity(altitude_km)` | Circular orbit velocity in km/s. v = sqrt(mu/a) |
| `propagate_eci(elements, dt)` | Propagate in ECI frame. Returns (position, velocity) as Vec3 |
| `gmst(unix_timestamp)` | Greenwich Mean Sidereal Time in radians (IAU 1982 model) |
| `eci_to_ecef(pos_eci, unix_timestamp)` | ECI to ECEF via GMST rotation about Z axis |
| `ecef_to_eci(pos_ecef, unix_timestamp)` | Inverse of above |
| `eci_to_ecef_velocity(pos_eci, vel_eci, unix_timestamp)` | ECI velocity to ECEF (includes Earth rotation correction) |
| `ecef_to_geodetic(pos_ecef)` | ECEF (km) to lat/lon/alt on WGS84 ellipsoid (iterative Bowring method) |
| `geodetic_to_ecef(pos)` | Inverse of above |
| `distance_km(a, b)` | Euclidean distance between two Vec3 points |

## Data Types

Defined as `NamedTuple` for immutability:

```python
class Vec3(NamedTuple):
    x: float
    y: float
    z: float

# In lib/nodalarc/orbital.py:
class OrbitalElements(NamedTuple):
    semi_major_axis_km: float  # a = altitude + Earth radius
    inclination_rad: float     # i
    raan_rad: float            # Right Ascension of Ascending Node
    true_anomaly_rad: float    # at epoch
```

`GeoPosition` is also a NamedTuple with `lat_deg`, `lon_deg`, `alt_km`.

## Physical Constants

In `lib/nodalarc/constants.py`:

| Constant | Value | What it is |
|----------|-------|-----------|
| `EARTH_MU` | 398600.4418 km^3/s^2 | Earth gravitational parameter |
| `EARTH_RADIUS_KM` | 6371.0 km | Mean Earth radius |
| `SPEED_OF_LIGHT_KM_S` | 299792.458 km/s | Used for latency computation |
| `WGS84_A` | 6378.137 km | WGS84 semi-major axis |
| `WGS84_E2` | ~0.00669 | WGS84 first eccentricity squared |

## Coordinate Frames

The propagator works with three coordinate frames:

1. **ECI (Earth-Centered Inertial)**: fixed to the stars. Propagation math happens here. The perifocal-to-ECI rotation uses RAAN and inclination.

2. **ECEF (Earth-Centered Earth-Fixed)**: rotates with the Earth. Conversion from ECI uses GMST rotation about the Z axis.

3. **Geodetic**: latitude, longitude, altitude on the WGS84 ellipsoid. Conversion from ECEF uses the iterative Bowring method.

The pipeline:
```
OrbitalElements + dt
    -> propagate_eci()           -> ECI position + velocity
    -> eci_to_ecef()             -> ECEF position
    -> eci_to_ecef_velocity()    -> ECEF velocity
    -> ecef_to_geodetic()        -> lat/lon/alt
```

## How to Add a New Propagator

### Option 1: SGP4/SDP4 (TLE-based)

For higher fidelity using Two-Line Element sets. The `sgp4` Python package is already a project dependency.

1. Create `services/ome/propagator_sgp4.py`
2. Accept TLE strings and convert to the sgp4 Satrec object
3. Implement the same return signature:
   ```python
   def propagate_sgp4(
       satrec,
       epoch_unix: float,
       dt: float,
   ) -> tuple[Vec3, Vec3, GeoPosition]:
   ```
4. sgp4 returns ECI position/velocity directly. Reuse `eci_to_ecef()`, `eci_to_ecef_velocity()`, and `ecef_to_geodetic()` from the existing propagator for coordinate conversion.
5. Wire it in at `services/ome/event_stream.py` line 68 for TLE-mode constellations.

### Option 2: J2 Perturbation

Add secular drift from Earth's oblateness without the complexity of full SGP4. This corrects the two biggest errors in the circular Keplerian model: RAAN regression and argument of perigee drift.

Extend `propagate_eci()` with J2 secular terms:
```
dOmega/dt = -1.5 * n * J2 * (R_e/a)^2 * cos(i)
domega/dt = 0.75 * n * J2 * (R_e/a)^2 * (5*cos^2(i) - 1)
```
where J2 = 1.08263e-3 and n = mean motion.

Apply these as linear corrections to RAAN and true anomaly in `propagate_eci()`. No API change needed.

### Option 3: Numerical Integration

For arbitrary force models (drag, solar radiation pressure, third-body effects):

1. Create `services/ome/propagator_numerical.py`
2. Use RK4 or similar integrator on the equations of motion with your force model
3. Return the same `(Vec3, Vec3, GeoPosition)` tuple
4. Numerical integration is significantly slower per step. For large constellations, pre-compute trajectories for one orbital period and interpolate at runtime.

## Wiring a New Propagator In

The call site is `services/ome/event_stream.py` line 68:

```python
ecef, vel_eci, geo = propagate_keplerian(sat.elements, epoch_unix, dt)
```

To use a different propagator, change this import and call. For example, to select at runtime based on constellation mode:

```python
if constellation.mode == "tle":
    from ome.propagator_sgp4 import propagate_sgp4
    ecef, vel, geo = propagate_sgp4(sat.satrec, epoch_unix, dt)
else:
    from ome.propagator import propagate_keplerian
    ecef, vel, geo = propagate_keplerian(sat.elements, epoch_unix, dt)
```

The rest of the OME only uses ECEF position, ECEF velocity, and geodetic position. As long as your propagator returns those three values, everything downstream works unchanged.

## Testing a New Propagator

1. **Orbital period**: propagate for exactly one period. The satellite should return to within 1 km of its starting ECEF position (ECI position will be exact, ECEF drifts due to Earth rotation).

2. **Ground track**: propagate for several orbits. The ground track should show ascending/descending node crossings at the correct inclination and westward drift between orbits.

3. **Velocity consistency**: `orbital_velocity(altitude)` should match the magnitude of the velocity vector from `propagate_eci()`.

4. **Known positions**: for SGP4, compare against published propagation results from CelesTrak or Space-Track.

5. **OME integration**: run a short session and verify the VF shows satellites at plausible positions. Check that ISL links form and break at reasonable orbital geometry boundaries.
