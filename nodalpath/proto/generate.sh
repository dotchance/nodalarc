#!/bin/bash
# Regenerate gRPC proto stubs for nodalpath.
# Run from the project root: bash nodalpath/proto/generate.sh
set -euo pipefail

PROTO_DIR="nodalpath/proto"

python -m grpc_tools.protoc \
    -I "$PROTO_DIR" \
    --python_out="$PROTO_DIR" \
    --grpc_python_out="$PROTO_DIR" \
    "$PROTO_DIR/forwarding.proto"

# Fix relative import in generated grpc stub
sed -i 's/^import forwarding_pb2/from nodalpath.proto import forwarding_pb2/' \
    "$PROTO_DIR/forwarding_pb2_grpc.py"

echo "Proto stubs regenerated in $PROTO_DIR"
