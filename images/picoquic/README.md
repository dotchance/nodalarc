# NodalArc picoquic image

A generic QUIC endpoint engine image: upstream
[picoquic](https://github.com/private-octopus/picoquic) (MIT) built at a
pinned commit, shipped as the `picoquicdemo` binary in a non-root,
zero-capability, read-only-rootfs container. The picoquic and picotls
license texts are in `/usr/share/doc` in the image.

The image is deliberately free of tuning and roles. A consuming workload
profile supplies everything per node as container arguments: server or
client mode, addresses, ports, and transport parameters.

The platform build (`make build`) builds the image as `nodalarc/picoquic`.
The shipped profiles reference it by a placeholder, as they do the FRR image.
