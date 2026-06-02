# Code Conventions

## Language and Tooling

- **Python 3.14+** for all backend services
- **TypeScript** (strict) for the frontend
- **Pydantic v2** for all structured data crossing component boundaries
- **pyroute2** for all kernel netlink operations - never shell out to `ip`, `tc`, `bridge`
- **f-strings** for formatting (except logging, which uses lazy format: `log.info("msg %s", val)`)
- **uv** for Python dependency management
- **Vitest** for frontend tests

## Pydantic Models

All event models, configuration objects, and inter-service data structures use Pydantic v2:

```python
from pydantic import BaseModel, ConfigDict

class LinkStateSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    snapshot_seq: int
    epoch_id: str
    links: list[LinkEntry]
```

`frozen=True` on all event models - events are immutable once created.

## NATS Subjects

All NATS subject strings are defined in `lib/nodalarc/nats_channels.py`. No literal subject strings anywhere else in the codebase.

```python
# Good
from nodalarc.nats_channels import SUBJECT_VISIBILITY_EVENT
await nc.publish(SUBJECT_VISIBILITY_EVENT, data)

# Bad - literal strings
await nc.publish("nodalarc.ome.visibility", data)
```

## Error Handling

**Fail loudly.** Every error must be visible. No silent swallowing:

```python
# Good - error is visible
except Exception:
    logging.exception("Publisher thread died")
    raise SystemExit(1)

# Bad - error disappears
except Exception:
    pass

# Bad - error hidden behind a default
except Exception:
    return 0
```

Only catch specific exceptions you can actually handle. Let everything else propagate.

## No Abstractions

No `EventBus`, `MessageRouter`, `ConfigManager`, `ServiceRegistry`, or similar abstraction layers. Direct function calls. Direct NATS publish/subscribe. Direct import.

If you're writing a class whose only purpose is to wrap another class, you're adding indirection without value. The NATS client is already an event bus. The Python module system is already a service registry.

## No New Dependencies

Only use libraries already in the project. If you think you need a new dependency, discuss it first. The dependency list is deliberately minimal to keep the build fast and the attack surface small.

## Logging

Use Python's standard `logging` module. Log levels:

| Level | Use |
|-------|-----|
| ERROR | Something broke and needs human attention |
| WARNING | Unexpected but handled without changing truth semantics |
| INFO | State transitions, lifecycle events, batch summaries |
| DEBUG | Per-item details, useful during development only |

```python
import logging
log = logging.getLogger(__name__)

log.info("Reconciling %d links", len(desired))
log.error("Node agent unreachable: %s", node_id)
```

## Comments

Default to no comments. Only add one when the WHY is non-obvious:

```python
# Good - explains a non-obvious constraint
# setns() not NetNS() - fork inherits signal handlers, causes port conflicts on SIGTERM
with _in_namespace(pid, fn):
    ...

# Bad - restates what the code does
# Reconcile the links
_reconcile_links(desired, nc)

# Bad - references the task that added it
# Added for the session switch fix (issue #47)
state_policy = DeliverPolicy.LAST_PER_SUBJECT
```

## Git

- Never commit directly to main - always use feature branches
- No conventional commit prefixes (`feat:`, `fix:`, `chore:`)
- No boilerplate attribution, generated footer lines, local notes, or editor metadata
- Commit specific files, not `git add .`
- Commit messages: what changed and why (imperative mood)
- Use: `git -c user.name='Your Name' -c user.email='you@example.com' commit`

## File Organization

- One responsibility per file. If a file is doing three different things, split it.
- Imports at the top, constants next, then functions/classes
- No circular imports - if A imports B and B needs A, refactor
- Keep files under 500 lines. If a file grows past this, it's doing too much.

## Frontend Conventions

- React 19 with hooks (no class components)
- React Three Fiber for 3D rendering, with Three.js objects owned by React lifecycle boundaries
- State management via React hooks plus narrow module-level registries for shared renderer facts
- TypeScript strict mode - no `any`, no `@ts-ignore`
- Shared geometries for batched rendering (O(1) draw calls); dispose caller-owned GPU resources on dependency change and unmount

## What NOT to Do

- Don't add feature flags or backwards-compatibility shims
- Don't add error handling for scenarios that can't happen
- Don't add abstraction layers (EventBus, MessageRouter, etc.)
- Don't shell out to system commands - use pyroute2 for netlink, native Python for everything else
- Don't use `asyncio.sleep()` in the OME pacing thread (causes satellite jitter)
- Don't use `pyroute2.NetNS()` - it forks. Use `setns()` via `namespace_ops.py`
- Don't put NATS subject literals in service code - import from `nats_channels.py`
- Don't add new dependencies without discussion
- Don't mock what you can run (prefer real objects in tests)
- Don't force-add ignored files or local workspace artifacts
