#!/usr/bin/env bash
# Runtime image inventory and image-related helper commands.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TAG="${TAG:-dev}"

resolve_mode() {
    local record
    if [ "${NA_IMAGES_NO_CLUSTER:-0}" = "1" ]; then
        if [ -n "${REGISTRY_PREFIX:-}" ] && [ -z "${REGISTRY_HOST:-}" ]; then
            echo "na-images: REGISTRY_PREFIX is set without REGISTRY_HOST; set REGISTRY_HOST instead" >&2
            exit 2
        fi
        if [ "${MODE:-auto}" = "single-node" ]; then
            MODE_RESOLVED="single-node"
            REGISTRY_HOST_RESOLVED=""
            REGISTRY_PREFIX_RESOLVED=""
        elif [ -n "${REGISTRY_HOST:-}" ]; then
            MODE_RESOLVED="multi-node"
            REGISTRY_HOST_RESOLVED="$REGISTRY_HOST"
            REGISTRY_PREFIX_RESOLVED="${REGISTRY_HOST}/"
        else
            MODE_RESOLVED="single-node"
            REGISTRY_HOST_RESOLVED=""
            REGISTRY_PREFIX_RESOLVED=""
        fi
        NODE_COUNT=0
        MIRROR_THIRD_PARTY_RESOLVED="${MIRROR_THIRD_PARTY:-0}"
    else
        record="$(bash "$ROOT_DIR/tools/na-mode.sh")"
        IFS=$'\t' read -r MODE_RESOLVED REGISTRY_HOST_RESOLVED REGISTRY_PREFIX_RESOLVED NODE_COUNT MIRROR_THIRD_PARTY_RESOLVED <<< "$record"
    fi
}

prefix_ref() {
    printf '%snodalarc/%s:%s\n' "$REGISTRY_PREFIX_RESOLVED" "$1" "$2"
}

nats_image() {
    printf '%s\n' 'nats:2.11-alpine'
}

nats_box_image() {
    printf '%s\n' 'natsio/nats-box:0.19.3'
}

emit_record() {
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$4" "$5" "$6"
}

list_build_images() {
    emit_record build nodalarc base "$(prefix_ref base "$TAG")" required built
    emit_record build nodalarc frr "$(prefix_ref frr "$TAG")" required built
    emit_record build nodalarc probe "$(prefix_ref probe "$TAG")" required built
    emit_record build nodalarc nodalpath-fwd "$(prefix_ref nodalpath-fwd "$TAG")" required built
    emit_record build nodalarc ome "$(prefix_ref ome "$TAG")" required built
    emit_record build nodalarc scheduler "$(prefix_ref scheduler "$TAG")" required built
    emit_record build nodalarc node-agent "$(prefix_ref node-agent "$TAG")" required built
    emit_record build nodalarc vs-api "$(prefix_ref vs-api "$TAG")" required built
    emit_record build nodalarc operator "$(prefix_ref operator "$TAG")" required built
    emit_record build nodalarc vf "$(prefix_ref vf "$TAG")" required built
    emit_record build nodalarc nodalpath "$(prefix_ref nodalpath "$TAG")" required built
}

list_platform_runtime_images() {
    emit_record platform nodalarc ome "$(prefix_ref ome "$TAG")" required built
    emit_record platform nodalarc scheduler "$(prefix_ref scheduler "$TAG")" required built
    emit_record platform nodalarc node-agent "$(prefix_ref node-agent "$TAG")" required built
    emit_record platform nodalarc vs-api "$(prefix_ref vs-api "$TAG")" required built
    emit_record platform nodalarc operator "$(prefix_ref operator "$TAG")" required built
    emit_record platform nodalarc vf "$(prefix_ref vf "$TAG")" required built
    emit_record platform nodalarc nodalpath "$(prefix_ref nodalpath "$TAG")" required built
}

list_session_runtime_images() {
    emit_record session nodalarc frr "$(prefix_ref frr "$TAG")" required built
    emit_record session nodalarc probe "$(prefix_ref probe "$TAG")" required built
    emit_record session nodalarc nodalpath-fwd "$(prefix_ref nodalpath-fwd "$TAG")" required built
}

list_third_party_runtime_images() {
    emit_record third-party external nats "$(nats_image)" required pulled
    emit_record third-party external nats-box "$(nats_box_image)" required pulled
}

list_optional_images() {
    emit_record optional nodalarc measurement "$(prefix_ref measurement "$TAG")" optional built
}

list_nodalarc_runtime_images() {
    list_platform_runtime_images
    list_session_runtime_images
}

list_all_runtime_images() {
    list_nodalarc_runtime_images
    list_third_party_runtime_images
}

image_for_tag() {
    local name="$1"
    local tag="$2"
    case "$name" in
        base|frr|probe|nodalpath-fwd|ome|scheduler|node-agent|vs-api|operator|vf|nodalpath|measurement)
            prefix_ref "$name" "$tag"
            ;;
        nats)
            nats_image
            ;;
        nats-box)
            nats_box_image
            ;;
        *)
            echo "na-images: unknown logical image '$name'" >&2
            exit 2
            ;;
    esac
}

image_for() {
    image_for_tag "$1" "$TAG"
}

helm_image_args() {
    local pull_policy
    if [ "$MODE_RESOLVED" = "single-node" ]; then
        pull_policy="Never"
    else
        pull_policy="IfNotPresent"
    fi

    printf '%s\n' "--set-string=buildTag=$TAG"
    printf '%s\n' "--set-string=imagePullPolicy=$pull_policy"
    printf '%s\n' "--set-string=images.frr=$(image_for frr)"
    printf '%s\n' "--set-string=images.probe=$(image_for probe)"
    printf '%s\n' "--set-string=images.nodalpathFwd=$(image_for nodalpath-fwd)"
    printf '%s\n' "--set-string=images.ome=$(image_for ome)"
    printf '%s\n' "--set-string=images.scheduler=$(image_for scheduler)"
    printf '%s\n' "--set-string=images.nodeAgent=$(image_for node-agent)"
    printf '%s\n' "--set-string=images.vsApi=$(image_for vs-api)"
    printf '%s\n' "--set-string=images.operator=$(image_for operator)"
    printf '%s\n' "--set-string=images.vf=$(image_for vf)"
    printf '%s\n' "--set-string=images.nodalpath=$(image_for nodalpath)"
    printf '%s\n' "--set-string=images.nats=$(image_for nats)"
    printf '%s\n' "--set-string=images.natsBox=$(image_for nats-box)"
}

usage() {
    cat <<'EOF'
usage: na-images.sh COMMAND

Commands:
  list-build-images
  list-platform-runtime-images
  list-session-runtime-images
  list-third-party-runtime-images
  list-nodalarc-runtime-images
  list-all-runtime-images
  list-optional-images
  image-for NAME
  image-for-tag NAME TAG
  helm-image-args
EOF
}

command="${1:-}"
if [ -z "$command" ]; then
    usage >&2
    exit 2
fi

resolve_mode

case "$command" in
    list-build-images) list_build_images ;;
    list-platform-runtime-images) list_platform_runtime_images ;;
    list-session-runtime-images) list_session_runtime_images ;;
    list-third-party-runtime-images) list_third_party_runtime_images ;;
    list-nodalarc-runtime-images) list_nodalarc_runtime_images ;;
    list-all-runtime-images) list_all_runtime_images ;;
    list-optional-images) list_optional_images ;;
    image-for)
        if [ -z "${2:-}" ]; then
            echo "na-images: image-for requires a logical image name" >&2
            exit 2
        fi
        image_for "$2"
        ;;
    image-for-tag)
        if [ -z "${2:-}" ] || [ -z "${3:-}" ]; then
            echo "na-images: image-for-tag requires a logical image name and tag" >&2
            exit 2
        fi
        image_for_tag "$2" "$3"
        ;;
    helm-image-args) helm_image_args ;;
    -h|--help|help) usage ;;
    *)
        echo "na-images: unknown command '$command'" >&2
        usage >&2
        exit 2
        ;;
esac
