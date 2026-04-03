# Time Modes

Nodal Arc supports two time modes that control how simulation time advances relative to wall clock time.

## Discrete-Event Mode

**Config:** `time.mode: discrete-event`

In discrete-event mode, the simulation clock advances by processing events from the pre-computed OME timeline file. Time jumps instantly from one event to the next. There is no waiting between events.

### How It Works

1. The OME pre-computes the full orbital timeline (positions, visibility changes) for the session duration
2. The Scheduler reads events sequentially from the timeline file
3. For each event, it applies link changes (create/destroy veth pairs, update tc shaping)
4. After applying changes, the convergence gate waits for FRR to converge before advancing
5. Time advances to the next event only after convergence is confirmed

### When to Use

- **Testing and development**: Fast iteration. A 95-minute orbital period completes in minutes
- **Automated scenarios**: Deterministic, reproducible results
- **Protocol comparison**: Same event sequence applied to different routing stacks
- **CI/CD pipelines**: No wall-clock dependency

### Configuration

```yaml
time:
  mode: discrete-event
  step_seconds: 1              # Minimum time granularity
  latency_update_interval_seconds: 10  # Latency recomputation frequency
```

In discrete-event mode, events are processed with zero delay between timeline entries.

## Real-Time Mode

**Config:** `time.mode: realtime`

In real-time mode, the simulation clock tracks wall clock time, optionally compressed by a factor. Protocol timers run at their natural cadence (or compressed proportionally).

### How It Works

1. The OME computes positions and visibility in real time (or compressed time)
2. Events are published on NATS JetStream as they occur
3. The Scheduler applies link changes when visibility events arrive
4. FRR protocol timers run normally (IS-IS hello intervals, OSPF dead timers, etc.)
5. Link latencies are recomputed periodically based on current orbital positions

### When to Use

- **Observing natural protocol behavior**: Hello timers, hold timers, and SPF computation happen at real cadence
- **Ground station handover**: Natural satellite pass timing drives handover events
- **Demonstrations**: Real-time 3D visualization of constellation operation

### Configuration

```yaml
time:
  mode: realtime
  compression: 10              # 10x faster than real time
  start_time: "2026-01-01T00:00:00Z"
  latency_update_interval_seconds: 10
```

### Compression Limits

Protocol timers have minimum values that limit how much compression is practical:

| Protocol | Timer | Minimum | Max Practical Compression |
|----------|-------|---------|--------------------------|
| IS-IS | Hello interval | 1s | ~10x |
| IS-IS | Hold time | 3s | ~10x |
| OSPF | Hello interval | 1s | ~10x |
| OSPF | Dead interval | 4s | ~10x |
| BFD | Tx interval | 50ms | ~100x |

At higher compression factors, protocol timers fire faster than the routing daemon can process them, leading to spurious adjacency flaps.

## Comparing Modes: Scenario 8

The `time-mode-validation` scenario validates that discrete-event mode produces routing behavior representative of real-time mode.

### Procedure

1. Deploy with IS-IS in discrete-event mode:

```bash
sudo make session DEFAULT_SESSION=configs/sessions/starlink-early-44-isis-striped.yaml
```

2. Run the time-mode-validation scenario:

```bash
uv run python -m tools.na_scenario \
  --scenario configs/scenarios/time-mode-validation.yaml
```

3. Record the session database path, tear down.

4. Deploy with IS-IS in real-time mode (use `starlink-early-44-isis-flat.yaml` or create a session with `time.mode: realtime`).

5. Run the same scenario.

6. Compare with `na-compare`:

```bash
uv run python -m tools.na_compare \
  --sessions /path/to/de-session/nodalarc.db /path/to/rt-session/nodalarc.db \
  --report convergence
```

### What to Look For

- **Convergence duration**: Discrete-event typically shows faster absolute convergence (no timer delays), but the relative ordering of convergence events should match
- **Packet loss**: Discrete-event may show lower packet loss (shorter convergence window)
- **Route path selection**: Both modes should converge to the same forwarding paths for the same topology state
