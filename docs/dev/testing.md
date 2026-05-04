# Testing

## Unit Tests

```bash
make test
```

This runs the full unit test suite (996+ tests) via pytest. All tests must pass before any commit touching backend code.

### Running specific tests

```bash
# Run a specific test file
uv run pytest tests/unit/test_scheduler_dispatcher.py -v

# Run tests matching a pattern
uv run pytest tests/unit/ -k "test_reconcile" -v

# Run with full output (no truncation)
uv run pytest tests/unit/test_ome_scheduler_contract.py -v --tb=long
```

### Critical test files

| File | What It Tests |
|------|--------------|
| `tests/unit/test_ome_scheduler_contract.py` | OME→Scheduler event contract. Must always pass. |
| `tests/unit/test_scheduler_dispatcher.py` | Reconcile logic, dispatch correctness |
| `tests/unit/test_node_agent_handlers.py` | BatchLinkUp/Down kernel operations |
| `tests/unit/test_coverage_preview.py` | Coverage preview pipeline (13 e2e tests) |
| `tests/unit/test_session_deployer.py` | Operator session creation logic |

### Do not use `-x`

Never run pytest with `-x` (stop at first failure). It produces a misleadingly low test count and hides whether other tests also fail. Run the full suite and address all failures.

## Frontend Tests

```bash
cd frontend
npm test
```

33+ tests covering React components and rendering logic. Must pass before any commit touching frontend code.

## Integration Tests

```bash
sudo make test-integration
```

Requires a running session. Tests exercise real NATS communication, real pod operations, and real routing state.

## What to Test

### For backend changes

1. **Unit tests pass** (`make test`)
2. **Deploy the change** (`sudo make deploy-<service>`)
3. **Verify behavior** in the running system:
   - For OME changes: check logs for event publishing, verify VF receives state
   - For Scheduler changes: verify links appear/disappear correctly, check Node Agent receives commands
   - For Node Agent changes: verify interfaces are wired, check routing adjacencies form
   - For VS-API changes: verify WebSocket data, REST endpoint responses
   - For Operator changes: deploy a new session, verify pod creation and config delivery

### For frontend changes

1. **Frontend tests pass** (`cd frontend && npm test`)
2. **TypeScript compiles** (`cd frontend && npx tsc --noEmit`)
3. **Deploy and test in browser** (`sudo make deploy-vf`, then check http://localhost:3000)
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

"I edited the code" ≠ "It works."

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

- Tests must exercise real code paths, not just isolated helpers
- Never present old test results as validation of new code
- Never mock what you can run (prefer real NATS, real models, real serialization)
- Tests that only verify internal state without observable behavior are low value
- Integration tests must cover ground stations - adjacency check + ping from GS pod
