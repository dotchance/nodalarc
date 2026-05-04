# Extending Propagators

The OME uses Keplerian orbital mechanics to compute satellite positions and velocities. The propagator is the function that maps `(orbital_elements, time)` → `(position, velocity)`.

## Current Implementation

The default propagator in `services/ome/propagator.py` implements two-body Keplerian propagation:

- Input: semi-major axis, eccentricity, inclination, RAAN, argument of perigee, mean anomaly at epoch
- Output: ECEF position (x, y, z) and velocity (vx, vy, vz) at any time t
- Coordinate frame: ECEF (Earth-Centered Earth-Fixed) for position, ECI for internal computation

This is sufficient for LEO/MEO circular orbits where J2 perturbation effects are acceptable over one orbital period (~95 minutes). The OME recomputes each window, so perturbation drift doesn't accumulate.

## When You'd Want a Different Propagator

- **High-fidelity requirements** - J2, J4, atmospheric drag, solar radiation pressure
- **GEO orbits** - station-keeping effects matter over longer periods
- **TLE/SGP4 input** - using real satellite TLEs instead of parametric constellation definitions
- **Interplanetary** - different gravity models entirely

## Propagator Interface

The propagator must implement:

```python
def propagate(
    orbital_elements: OrbitalElements,
    epoch: datetime,
    target_time: datetime
) -> tuple[Position, Velocity]:
    """
    Compute position and velocity at target_time.

    Returns:
        position: (x, y, z) in km, ECEF frame
        velocity: (vx, vy, vz) in km/s, ECEF frame
    """
```

And for batch computation (all satellites at one time):

```python
def propagate_batch(
    elements: list[OrbitalElements],
    epoch: datetime,
    target_time: datetime
) -> list[tuple[Position, Velocity]]:
    """Propagate all satellites to target_time."""
```

## Integration Points

### OME (window computation)

`services/ome/event_stream.py` calls the propagator at discrete time steps to compute all satellite positions, then checks visibility between pairs. Replace the propagator call here.

### Scheduler (latency updates)

`services/scheduler/latency.py` propagates active link endpoints locally on a 10-second interval to compute ranges and latencies. Uses the same propagator interface.

### VS-API and VF (position display)

The `SessionEphemeris` published by the OME contains Keplerian orbital elements. The VF and VS-API run local propagation from these elements. If you use a non-Keplerian propagator, you need to either:
- Publish position data per-tick (expensive, changes the architecture)
- Provide a JavaScript implementation of your propagator for the VF
- Accept lower-fidelity Keplerian positions in the UI while the backend uses your propagator

## Adding SGP4/TLE Support

The most common extension: use NORAD TLEs as input instead of parametric constellation definitions.

1. Add a constellation mode `tle` that accepts a TLE file
2. Parse TLEs into `OrbitalElements` for the existing pipeline, OR
3. Replace the propagator with SGP4 (via `sgp4` Python library, already common)
4. Update `SessionEphemeris` to carry TLE data instead of Keplerian elements
5. Update the VF's `simClock.ts` to use `satellite.js` (JavaScript SGP4) for local propagation

The sgp4 library is a clean swap - same input/output contract, higher fidelity for real satellite tracking.
