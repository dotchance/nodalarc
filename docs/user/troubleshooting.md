# Troubleshooting

Common issues you might encounter when using NodalArc and how to resolve them.

## Visualization Won't Load

**Symptom:** Browser shows a blank page or connection error at http://localhost:3000.

**Causes:**
- The platform isn't running yet. Ask your administrator to verify the deployment.
- If running on a remote machine, port 3000 needs to be forwarded or accessible from your network.
- Check if your browser blocks WebSocket connections (corporate proxies sometimes do).

**Try:** Refresh the page. If the problem persists, check with whoever deployed the system.

## No Satellites Visible

**Symptom:** The globe loads but no satellites appear.

**Causes:**
- No session is deployed. Use the session wizard to create one.
- The session is still deploying. Wait 1-2 minutes for pods to come online.
- Zoom level - you may be zoomed too far in or out to see satellites at their altitude.

**Try:** Scroll out until you can see the full globe. Check the session status indicator in the toolbar.

## Links Not Showing

**Symptom:** Satellites visible but no ISL or ground links drawn.

**Causes:**
- Links may be toggled off. Press **L** for ISL links, **G** for ground links.
- The routing protocol is still converging. Wait 30-60 seconds after session deployment.
- The constellation may be configured with a satellite type that has limited ISL range - satellites may be too far apart.

**Try:** Press L and G to ensure links are enabled. Wait for convergence. Check the event log for link_up events.

## Terminal Not Responding

**Symptom:** Terminal tab opens but no prompt appears, or commands hang.

**Causes:**
- The target pod may still be starting up. Wait a few seconds and try again.
- WebSocket connection dropped. Close the tab and reopen.

**Try:** Close the terminal tab and select the node again. If persistent, refresh the page.

## Session Won't Deploy

**Symptom:** Session wizard shows an error or deployment hangs.

**Causes:**
- The cluster may not have enough resources for the selected constellation size. Smaller constellations (36, 66) require less resources.
- A previous session may not have cleaned up completely. Ask your administrator to run a teardown.

**Try:** Start with a smaller constellation (Demo-36). If it works, the issue is resource limits.

## Routing Not Converging

**Symptom:** Path traces fail, pings timeout, no routing neighbors showing in the terminal.

**Causes:**
- Normal convergence takes 10-30 seconds. Large constellations (176+ satellites) may take up to 60 seconds.
- If using a per-plane area strategy, inter-area routes take longer to propagate than intra-area routes.

**Try:** Wait 60 seconds, then check `show isis neighbor` or `show ip ospf neighbor` in the terminal. If adjacencies are forming (state = Up), routing is converging. If no adjacencies after 60 seconds, ask your administrator to check the Node Agent logs.

## Simulation Appears Frozen

**Symptom:** Satellites not moving, no new events in the log.

**Causes:**
- The simulation might be paused. Press **Space** to resume.
- The OME may have restarted. It will resume automatically.

**Try:** Press Space. Check the playback indicator in the toolbar (paused/playing state).

## High Browser CPU Usage

**Symptom:** Browser tab using significant CPU, system fan running.

**Causes:**
- Large constellations with all visual elements enabled (trails, paths, links) require more GPU work.
- 4K displays render 4x the pixels of 1080p.

**Try:** Toggle off satellite trails (T), orbital paths (P), and labels (;) to reduce rendering load. The visualization is optimized for smooth performance but very large constellations with all effects enabled will use more resources.

## Getting Help

If your issue isn't covered here:

- Check with your system administrator - many issues are deployment-related
- Look at the browser developer console (F12) for JavaScript errors
- File an issue on the GitHub repository with steps to reproduce
