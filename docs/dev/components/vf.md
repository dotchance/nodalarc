# VF - Visualization Frontend

**Location:** `frontend/`
**Deployment:** Kubernetes Deployment (nginx serving static assets)
**Port:** 3000
**Stack:** React 19, Three.js, TypeScript, Vite

## Responsibility

The VF renders the constellation visualization in the browser: 3D globe with satellite positions, ISL links, ground stations, topology graph, event log, terminal access, and session wizard.

## Architecture

```
VS-API WebSocket (~1 Hz) ──→ React state ──→ Three.js scene
                                │
SessionEphemeris ──→ simClock ──→ Keplerian propagation ──→ satellite positions (60fps)
```

Key principle: the VF computes satellite positions locally from orbital elements. It does NOT receive per-frame position data from the server. This means:
- 60fps rendering with 1 Hz server updates
- Zero bandwidth for position data regardless of constellation size
- Smooth interpolation between server ticks

## Rendering Architecture (O(1) Draw Calls)

All visual elements are batched into shared geometries. Draw call count is constant regardless of constellation size:

| Element | Implementation | Draw Calls |
|---------|---------------|:---:|
| ISL links | Single `LineSegments2` batch (bowed arcs, vertex colors) | 1 |
| Ground links | Same batch as ISL (straight lines) | 0 (shared) |
| Satellite trails | Single `LineSegments` batch (ring buffer, additive blend) | 1 |
| Country boundaries | Single `LineSegments` batch (static after load) | 1 |
| Orbital paths | Single `LineSegments2` batch (per-plane colors) | 1 |
| Satellites | `InstancedMesh` | 1 |
| Ground stations | `InstancedMesh` | 1 |
| Globe | Standard mesh | ~10 |
| UI overlays | HTML/CSS | ~30 |
| **Total** | | **~47** |

Previous architecture: per-link `Line2` objects (261), per-satellite trail `THREE.Line` (86), per-polygon boundary `THREE.Line` (275), per-satellite orbit `Line2` (90) = 937 draw calls/frame.

### Key rendering files

| File | Content |
|------|---------|
| `src/globe/links.ts` | ISL + ground link batch (LineSegments2, bowed arcs, NaN masking) |
| `src/globe/orbitalTrails.ts` | Trail batch (ring buffer, zero-alloc, per-vertex fade) |
| `src/globe/boundaries.ts` | Country border batch (static LineSegments) |
| `src/globe/allOrbits.ts` | Orbital path batch (per-plane vertex colors) |

### NaN Masking

Hidden links/trails are masked by writing `NaN` to position buffer entries. The GPU clipper discards NaN vertices before rasterization - zero cost for hidden geometry. No JavaScript array manipulation needed to add/remove links.

### Buffer Growth

Link and trail buffers grow dynamically (2x headroom) when more elements appear than initially allocated. Growth copies existing data to preserve visual state.

## simClock

`src/sim/simClock.ts` - single authoritative clock consumed by satellite interpolation and Earth rotation.

- Adaptive EMA filter smooths server-reported sim_time
- Outlier detection: if 3 consecutive snapshots are clamped as outliers, re-seed the clock
- `setPlaybackPaused()` freezes all time-dependent rendering

## WebSocket Connection

`src/hooks/useWebSocket.ts` - manages VS-API WebSocket lifecycle:

1. Connect to `ws://host:8080/ws/v1/state?token=<token>`
2. First message: `SessionEphemeris` → stored for local propagation
3. Subsequent messages: `StateSnapshot` at ~1 Hz → update React state
4. On disconnect: reconnect with backoff

## State Management

React hooks + module-level singletons:

- **React state** - UI state (selected node, panel visibility, color mode, toggles)
- **Module singletons** - rendering state (Three.js objects, buffer positions, trail ring buffers)

The rendering layer (Three.js) operates outside React's render cycle. React sets flags; the animation loop reads them. This avoids re-rendering the entire component tree at 60fps.

## Key Bindings

`src/hooks/useKeyboard.ts` - keyboard shortcut handler. Disabled when focus is in an input field.

| Key | Action |
|-----|--------|
| Space | Pause/resume |
| Tab | Toggle globe/topology view |
| T | Toggle trails |
| V | Top view |
| P | Toggle orbital paths |
| L | Toggle ISL links |
| G | Toggle ground links |
| F | Follow selected node |
| H | Toggle historical mode |
| N | Toggle globe mode |
| I | Toggle reference frame |
| 1/2 | Color modes |

## Development

```bash
# Hot reload dev server
cd frontend
npm run dev

# Type check
npx tsc --noEmit

# Tests
npm test

# Production build + deploy
make deploy-vf
```

## Key Directories

```
src/
├── App.tsx              Main app, state management, keyboard actions
├── globe/              3D globe rendering (Three.js)
│   ├── GlobeView.tsx   Scene setup, animation loop
│   ├── links.ts        ISL/ground link batch
│   ├── orbitalTrails.ts  Trail ring buffer batch
│   ├── allOrbits.ts    Orbital path batch
│   └── boundaries.ts   Country border batch
├── topology/           2D topology graph view
├── sim/                Simulation clock, Keplerian propagation
├── hooks/              React hooks (WebSocket, keyboard, etc.)
├── components/         UI components (panels, wizard, terminal)
└── types.ts            Shared TypeScript types
```
