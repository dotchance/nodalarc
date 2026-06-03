# Extending Propagators

The OME propagates node positions and velocities, then evaluates visibility and
latency from those positions. A propagator maps a node's orbital state and
simulation time to a common-frame position and velocity.

## Current Implementation

NodalArc currently supports:

- Mean-element propagation for catalog constellations
- Body-parameterized propagation for Earth and Luna sessions
- Local Skyfield/JPL BSP ephemeris for body positions in Earth-Luna sessions
- TLE/SGP4 parsing support in the model layer, with runtime use gated by the
  resolver until the full workflow is ready

Earth-only LEO/MEO/GEO sessions do not need a body ephemeris manifest. Lunar
sessions do: the session declares a local BSP kernel, and the resolver validates
path, checksum, coverage, provider, and required body targets before OME starts.
Runtime network fetch of ephemeris kernels is forbidden.

## Frames

The physics contract is common-frame first:

1. Propagate the node in its segment/body frame.
2. Add the body's common-frame position and velocity when the central body is
   not the common origin.
3. Publish enough ephemeris and frame metadata for clients to render positions
   locally.

For Earth-Luna sessions, the common frame is Earth-centered GCRS. A lunar node's
common-frame velocity must include the Moon's velocity, not only the node's
velocity around Luna.

## When You'd Want a Different Propagator

- **High-fidelity Earth orbit** - J2/J4, drag, solar radiation pressure,
  station-keeping
- **TLE/SGP4 input** - real satellite TLE catalogs rather than parametric
  constellations
- **Lagrange or deep-space relays** - explicit external ephemeris or SPK-backed
  state vectors
- **Mars or other bodies** - additional body constants, body frames, surface
  models, and ephemeris targets

Unsupported propagation grammar must fail validation. Do not approximate a
future propagator with the nearest current one.

## Propagator Interface

At the runtime boundary, propagation needs:

```python
def propagate(
    orbital_elements: OrbitalElements,
    epoch_unix: float,
    target_unix: float,
    *,
    central_body: str,
    body_state: CommonBodyState | None,
) -> tuple[Position, Velocity]:
    """
    Compute common-frame position and velocity at target_unix.
    """
```

Batch propagation should keep the same contract and return deterministic output
for the same session, epoch, and target time.

## Integration Points

### Resolver

`lib/nodalarc/resolve_session.py` validates whether the requested propagator,
body, and ephemeris provider are runtime-supported. It also materializes the
body ephemeris provider for the OME when required.

### OME

`services/ome/event_stream.py`, `services/ome/propagation_engine.py`, and the
visibility engines use the propagated positions to build link authority. OME
payloads carry authoritative range and latency. The Scheduler does not invent
geometry if OME did not provide it.

### VS-API and VF

The frontend receives `SessionEphemeris` and computes render positions locally.
Any new runtime propagator needs a matching frontend propagation path or a new
wire contract. Publishing per-frame positions is a different architecture and
should not be added as a hidden fallback.

## Adding SGP4/TLE Support

TLE/SGP4 support should be added as a complete workflow:

1. Accept a TLE constellation source in the segment grammar
2. Validate TLE file presence, epoch, body, and runtime support in the resolver
3. Use SGP4 in the OME for authority
4. Publish TLE-backed ephemeris records to clients
5. Use a matching client-side SGP4 implementation for visualization

Until those pieces are present, TLE controls in the UI should be disabled or
marked as coming soon. They should not generate YAML that the runtime cannot
execute.
