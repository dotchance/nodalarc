# NodalArc ION image

A container image packaging NASA JPL's Interplanetary Overlay Network
(ION-DTN), release 4.2.0, built from the upstream source without
modification (BSD-3-Clause in Caltech's wording, see
`/opt/ion/share/doc/ion/license.txt` in the image).

The image contains ION's daemons, administration programs, convergence
layers and bundle applications as ION installs them, and `ion-node`, a
launcher that writes one node's ION configuration and contact plan from
environment variables and runs the node over LTP on UDP. One environment
describes a whole DTN network; every node runs with it and finds itself by
its own address. The script's header documents the variables. The image
contains no network: a consuming workload profile supplies it.

The platform build (`make build`) builds the image as `nodalarc/ion`. The
shipped profiles reference it by a placeholder, as they do the FRR image.

Upstream: <https://github.com/nasa-jpl/ION-DTN>
