# Teardown and Cleanup

## Switching Sessions

The typical way to switch sessions is through the session wizard in the UI. Select a new constellation, routing stack, and ground stations, then deploy. The platform handles the transition automatically.

From the command line:

```bash
sudo make session DEFAULT_SESSION=configs/sessions/starlink-176-nodalpath.yaml
```

No teardown needed to switch sessions.

## Full Teardown

When you need to take everything down (done for the day, something went wrong, or any reason you want the platform completely stopped):

```bash
sudo make teardown
```

This runs `tools/na-teardown.sh` which executes a specific 9-step sequence. The order matters because doing it out of order causes pods and namespaces to get stuck on finalizers.

The sequence:

1. Strip finalizers from session resources and delete them
2. Wait for session pods to terminate (force-deletes stuck pods after 60s)
3. Clean kernel state on all nodes via the Node Agent pods (VXLAN tunnels, veth pairs, bridges)
4. Helm uninstall (removes all platform pods)
5. Wait for Node Agent pods to terminate
6. Delete namespace (forces through stuck finalizers if needed)
7. Delete cluster-scoped resources (CRD, roles, bindings)
8. Final local kernel state cleanup (belt and suspenders)
9. Verify clean state

The script handles every failure mode: stuck pods, stuck finalizers, stuck namespaces, partially deployed sessions, crashed operators. If it reports "Teardown complete" the system is clean.

To bring the platform back up after a teardown:

```bash
sudo make install session
```

## Cleanup Levels

| Command | What it does | When to use |
|---------|-------------|-------------|
| `make clean` | Removes frontend build output and Python caches | Force a frontend rebuild |
| `make clean-images` | Removes all nodalarc Docker images and build cache | Force a full image rebuild |
| `make clean-deps` | Removes Python .venv and node_modules | Force a full dependency reinstall |
| `sudo make nuke` | All of the above plus full teardown | Start completely fresh, as if from a new checkout |
