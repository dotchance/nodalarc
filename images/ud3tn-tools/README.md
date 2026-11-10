# NodalArc uD3TN tools image

A container image packaging D3TN's `ud3tn-utils` AAP 2.0 command-line
tools: `aap2-config`, `aap2-send`, `aap2-receive`, `aap2-storage-agent`,
`aap2-ping`, and the rest of the published entry points (BSD-3-Clause OR
Apache-2.0).

The image is a generic client for a co-located uD3TN daemon. It contains
no daemon, no node identity, and no contact plan. A consuming workload
profile supplies the command, the AAP 2.0 socket path, and any peer
addresses through its own environment. Basic network diagnostics
(`ip`, `ping`, `tracepath`) are included so a DTN node can be inspected
from inside its own container. The uD3TN daemon itself runs from D3TN's
own published image.

The platform build (`make build`) builds the image as `nodalarc/ud3tn-tools`.
The shipped profiles reference it by a placeholder, as they do the FRR image.
