# Testing

## Unit Tests

```bash
make test
```

This runs the Python unit suite and the frontend suite. Both must pass before any commit.

A unit test checks one unit of NodalArc on its own and needs no cluster. The tests are grouped by
concept under `tests/unit`:

| Directory | What it covers |
|-----------|----------------|
| `sky` | Orbits, visibility, link scheduling, latency |
| `sessions` | Session grammar, catalog, resolver |
| `authoring` | Session Builder and Wizard services |
| `routing` | Router configuration and workload composition |
| `runtime` | Operator, Scheduler and Node Agent logic |
| `observation` | VS-API views, explanations, history, logging |
| `platform` | Make targets, lifecycle scripts, project metadata |
| `tools` | Report, compare and scenario tools |

A unit test may replace a collaborator only when all of these hold:

1. The collaborator is outside the process (the kernel, Kubernetes, NATS, SSH, another service), or the
   test needs a failure that cannot be produced on demand.
2. The stand-in answers with the collaborator's real types.
3. The test asserts what the unit decided or produced. It never asserts a value the stand-in was told
   to return.
4. The behavior a user can reach is also covered by an integration test.

### Running specific tests

```bash
# One directory
uv run pytest tests/unit/sky -q

# One file
uv run pytest tests/unit/sessions/test_resolve_session.py -v

# Tests matching a pattern
uv run pytest tests/unit -k "handover" -v
```

### Do not use `-x`

Never run pytest with `-x` (stop at first failure). It produces a misleadingly low test count and hides whether other tests also fail. Run the full suite and address all failures.

## Frontend Tests

```bash
cd frontend
npm test
```

The frontend test suite covers React components and rendering logic. It must pass before any commit touching frontend code.

## Integration Tests

```bash
make test-integration
```

Every integration test runs against a NodalArc cluster. Set `VS_API_HOST` to the VS-API address, or
let the tests find it the way the lifecycle scripts do.

The tests in `tests/integration/operator` use NodalArc the way a user does: the REST API, the state
feed, and the terminal as `operator`. They take their evidence from the emulated network: the routers'
own neighbor tables and packets sent between nodes.

`truths.py` holds what must be true of any ready session: the clock runs at the speed shown, the links
shown are the routers' neighbors, the latency shown is the delay packets get, the range shown is the
distance between the positions shown, and satellites move at the speed their orbits require. Each test
file covers one thing a user does and ends by checking every truth:

| File | What a user does |
|------|------------------|
| `test_truth.py` | Looks at the running session |
| `test_access.py` | Opens a terminal, runs commands, traces a path |
| `test_sessions.py` | Selects, runs and switches sessions |
| `test_time.py` | Pauses, changes speed, seeks |
| `test_topology.py` | Lets the sky move so links come and go |
| `test_authoring.py` | Authors a session with the Wizard or a YAML file and runs it |
| `test_catalog.py` | Runs every shipped session |

```bash
# About two minutes; changes nothing on the cluster
uv run pytest tests/integration/operator/test_truth.py tests/integration/operator/test_access.py -q

# Every shipped session; replaces the running session many times
uv run pytest tests/integration/operator -m catalog -q
```

`test_sessions.py`, `test_authoring.py` and `test_catalog.py` replace the session running on the cluster.
Each ends on the session it found.

## What to Test

### For backend changes

1. **Unit tests pass** (`make test`)
2. **Deploy the change** (`make deploy-<service>`)
3. **Verify behavior** in the running system:
   - For OME changes: check logs for event publishing, verify VF receives state
   - For Scheduler changes: verify links appear/disappear correctly, check Node Agent receives commands
   - For Node Agent changes: verify interfaces are wired, check routing adjacencies form
   - For VS-API changes: verify WebSocket data, REST endpoint responses
   - For Operator changes: deploy a new session, verify pod creation and config delivery

### For frontend changes

1. **Frontend tests pass** (`cd frontend && npm test`)
2. **TypeScript compiles** (`cd frontend && npx tsc --noEmit`)
3. **Deploy and test in browser** (`make deploy-vf`, then check http://localhost:3000)
4. **Test the golden path** - the primary feature works
5. **Test edge cases** - empty state, extreme zoom, large constellations
6. **Check for regressions** - other features still work

### For configuration changes

1. **Deploy a session** using the changed config
2. **Verify routing works** - adjacencies form, pings succeed
3. **Verify the visualization** - links appear, positions correct

## Verification Standards

**Never claim tests pass without running them and showing output.**

A code change is not verified until:
- Tests ran successfully (with output showing pass count)
- The change is deployed to the cluster (not just edited locally)
- The behavior is confirmed working (browser, logs, kubectl)

"I edited the code" != "It works."

### Proving a fix

After deploying a change:

```bash
# Verify the image contains your change
sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl exec deploy/nodalarc-scheduler -n nodalarc -- \
  grep "your_unique_string" /app/dispatcher.py

# Verify the behavior changed
sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl logs -l app=nodalarc-scheduler -n nodalarc | \
  grep "expected log output"
```

## Test Quality Rules

- A test states one thing NodalArc promises and shows evidence for it
- Integration tests act through what a user has and take evidence from the emulated network
- Unit tests meet the four conditions above before they replace a collaborator
- Never present old test results as validation of new code
- Never write a test that reports a pass when it could not run
