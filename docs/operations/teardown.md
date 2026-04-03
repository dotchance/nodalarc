# Teardown and Cleanup

NodalArc has several levels of cleanup depending on what you need.

## Stop a Running Session

```bash
sudo make teardown
```

This is the standard way to stop a session. It:

1. Stops the constellation session (deletes all satellite and ground station pods)
2. Cleans kernel state on all nodes (VXLAN tunnels, veth pairs, bridges)
3. Removes the platform (Helm uninstall)
4. Deletes the namespace and cluster-scoped resources
5. Verifies everything is clean

Wait for "Teardown complete" before starting a new session. The script handles stuck pods, stuck finalizers, and partially deployed states automatically. It works regardless of what state the system is in.

After teardown, start a new session with:

```bash
sudo make install session
```

## Remove Build Artifacts

```bash
make clean
```

Removes frontend build output (dist/) and Python caches. Does not touch Docker images or installed dependencies. Use this when you want to force a frontend rebuild.

## Remove Docker Images

```bash
make clean-images
```

Removes all nodalarc Docker images and the Docker build cache. Use this when you want to force a full image rebuild from scratch (for example, after updating a base image or Dockerfile).

## Remove Dependencies

```bash
make clean-deps
```

Removes the Python virtual environment (.venv) and all node_modules directories. Use this when you want to force a full dependency reinstall (for example, after changing package versions).

## Remove Everything

```bash
sudo make nuke
```

Does all of the above in one shot: teardown + clean + clean-images + clean-deps. After this, the system is back to a fresh checkout state. The next `make all` rebuilds everything from scratch.

## When to Use Each

| Situation | Command |
|-----------|---------|
| Done for the day, want to free resources | `sudo make teardown` |
| Want to run a different session | `sudo make teardown` then `sudo make session DEFAULT_SESSION=...` |
| Something is broken, want a clean restart | `sudo make teardown` then `sudo make install session` |
| Images seem stale or corrupted | `make clean-images` then `make build` |
| Dependencies seem wrong | `make clean-deps` then `make deps` |
| Nothing works, start completely fresh | `sudo make nuke` then `make all` |
