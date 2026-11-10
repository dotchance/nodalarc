# NodalArc HDTN image

A container image packaging NASA Glenn Research Center's High-rate Delay
Tolerant Networking (HDTN) implementation, release v3.0.0, built from the
upstream source without modification (Apache-2.0, see
`/opt/hdtn/share/doc/hdtn/LICENSE.md` in the image).

The image contains the `hdtn-one-process` bundle node and HDTN's bundle
tools (`bpgen-async`, `bpsink-async`, `bping`, `bpsendfile`,
`bpreceivefile`, `bpsendpacket`, `bpreceivepacket`, `bpfiletransfer`), and
`hdtn-node`, a launcher that writes one node's HDTN configuration and
contact plan from environment variables and runs the node over LTP. One
environment describes a whole DTN network; every node runs with it and finds
itself by its own address. The script's header documents the variables. The
image contains no network: a consuming workload profile supplies it.

The platform build (`make build`) builds the image as `nodalarc/hdtn`. The
shipped profiles reference it by a placeholder, as they do the FRR image.

Upstream: <https://github.com/nasa/HDTN>
