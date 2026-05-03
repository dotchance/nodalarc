# Time Controls

NodalArc runs the orbital simulation in real time by default — one second of simulation time equals one second of wall-clock time. You can pause, resume, and adjust the simulation speed to observe events at different time scales.

## Controls

| Action | How |
|--------|-----|
| Pause/Resume | Press **Space** |
| Speed up | Use the speed control in the UI toolbar |
| Slow down | Use the speed control in the UI toolbar |

## What Happens When You Pause

When the simulation is paused:

- Satellites freeze in their current positions
- No new link state changes occur (links don't appear or disappear)
- Existing routing state remains stable
- The visualization freezes (no motion)
- The terminal still works — you can inspect routing state, run commands, ping between nodes

Pause is useful when you want to inspect a specific moment in time without the topology changing under you. For example, pause right after a ground station handoff to examine the routing table before and after convergence.

## What Happens When You Change Speed

Increasing simulation speed makes the constellation orbit faster. At 10x speed:

- Satellites move 10x faster around their orbits
- Link state changes happen 10x more frequently
- Ground station handoffs occur 10x more often
- Routing convergence still happens at real time (FRR doesn't speed up)

This creates an interesting effect: at high simulation speeds, the topology changes faster than the routing protocol can converge. You'll see routing instability, flapping, and potentially unreachable destinations. This is realistic — it models what would happen if orbital mechanics moved faster than your routing protocol could adapt.

At slower speeds (0.5x, 0.25x), topology changes are spaced further apart in wall time. This gives you more time to observe and understand each individual event.

## Use Cases

- **Pause + inspect** — freeze after a specific event (handoff, link failure) and examine routing state across multiple nodes
- **Fast forward** — speed up to observe a full orbital period (95 minutes at 550 km) in a few minutes. See the complete cycle of link formation, ground station passes, and constellation geometry changes.
- **Slow motion** — slow down during a complex convergence event to watch the protocol react step by step
