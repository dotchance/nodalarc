# GitHub Repository Settings

This file records the GitHub UI settings that matter for repository discovery.
GitHub does not automatically apply these values from this file; set them in
the repository's About panel and Settings UI.

## About

Description:

```text
Satellite network emulator for orbital routing, handoffs, and moving topology.
```

Website:

```text
https://nodal.asmolab.net
```

Topics:

```text
networking
satellite
satcom
emulation
emulator
routing
orbital
space
leo
kubernetes
frr
ospf
isis
bgp
mpls
segment-routing
traffic-engineering
linux-networking
vxlan
network-visualization
```

## Social Preview

Upload this image in Settings -> General -> Social preview:

```text
docs/images/github-social-preview.png
```

## Discovery Notes

Keep the About description short. GitHub search weights the repository name,
description, topics, and README. The description should say what the project is
before it says how it is built.

Do not add `simulation` as a topic unless the project intentionally wants that
search traffic. NodalArc's useful distinction is emulation: real routers living
inside moving orbital geometry.

## Branch Protection

Require these checks before merging to `main`:

```text
Quality / Lint
CLA
```

The `CLA` status is set by `.github/workflows/cla.yml`. The workflow does not
check out pull request code; it only reads pull request metadata and comments,
then writes a commit status on the pull request head SHA.
