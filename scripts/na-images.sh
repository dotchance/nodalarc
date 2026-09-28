#!/usr/bin/env bash
# Runtime image inventory and image-related helper commands.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TAG="${TAG:-dev}"

# shellcheck source=scripts/na-lib.sh
. "$ROOT_DIR/scripts/na-lib.sh"
LIB_PREFIX="na-images"

resolve_mode() {
    local record
    if [ "${NA_IMAGES_NO_CLUSTER:-0}" = "1" ]; then
        record="$(bash "$ROOT_DIR/scripts/na-mode.sh" --no-cluster)"
    else
        record="$(bash "$ROOT_DIR/scripts/na-mode.sh")"
    fi
    mode_record_load "$record"
}

prefix_ref() {
    printf '%snodalarc/%s:%s\n' "$REGISTRY_PREFIX_RESOLVED" "$1" "$2"
}

nats_image() {
    printf '%s\n' 'nats:2.11-alpine@sha256:e4bf19f15fd3218814a4e3c9e0064e1334bd8aa20d5984b9f1a0afd084f8cc00'
}

nats_box_image() {
    printf '%s\n' 'natsio/nats-box:0.19.3@sha256:fbdf67cb49333afc50e2003c6857845d1ed9cf822d2b886cc5658ebf3c754b07'
}

# The one service inventory. One line per logical image:
#   name|helm image key|Kubernetes resource or -|memberships|required|source
# memberships: comma-separated subset names (build, platform, session,
# third-party, optional); source: built (a nodalarc image at the tree's
# tag) or pulled (a pinned upstream image). Every list command filters
# this table; nothing else names a service.
IMAGE_TABLE=(
    "base|base|-|build,session|required|built"
    "frr|-|-|build,session|required|built"
    "probe|-|-|build,session|required|built"
    "ome|ome|deployment/ome|build,platform|required|built"
    "scheduler|scheduler|deployment/nodalarc-scheduler|build,platform|required|built"
    "node-agent|nodeAgent|daemonset/nodalarc-node-agent|build,platform|required|built"
    "vs-api|vsApi|deployment/nodalarc-vs-api|build,platform|required|built"
    "operator|operator|deployment/nodalarc-operator|build,platform|required|built"
    "vf|vf|deployment/nodalarc-vf|build,platform|required|built"
    "nats|nats|deployment/nodalarc-nats|third-party|required|pulled"
    "nats-box|natsBox|-|third-party|required|pulled"
    "measurement|-|-|optional|optional|built"
)

table_field() {
    # table_field NAME INDEX -> the field, or exit 2 for an unknown name
    local name="$1" index="$2" entry
    for entry in "${IMAGE_TABLE[@]}"; do
        if [ "${entry%%|*}" = "$name" ]; then
            printf '%s\n' "$entry" | cut -d'|' -f"$index"
            return 0
        fi
    done
    echo "na-images: unknown logical image '$name'" >&2
    exit 2
}

image_for_tag() {
    local name="$1" tag="$2" source
    source="$(table_field "$name" 6)"
    case "$name" in
        nats) nats_image ;;
        nats-box) nats_box_image ;;
        *)
            [ "$source" = "built" ] || { echo "na-images: no image source for '$name'" >&2; exit 2; }
            prefix_ref "$name" "$tag"
            ;;
    esac
}

image_for() {
    image_for_tag "$1" "$TAG"
}

helm_key_for() {
    local key
    key="$(table_field "$1" 2)"
    if [ "$key" = "-" ]; then
        echo "na-images: no Helm image key for logical name '$1'" >&2
        exit 2
    fi
    printf '%s\n' "$key"
}

resource_for() {
    local resource
    resource="$(table_field "$1" 3)"
    if [ "$resource" = "-" ]; then
        echo "na-images: no Kubernetes resource for logical name '$1'" >&2
        exit 2
    fi
    printf '%s\n' "$resource"
}

emit_record() {
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$4" "$5" "$6"
}

list_members() {
    # list_members SCOPE -> the six-field records of every table entry in SCOPE
    local scope="$1" entry name memberships required source kind
    for entry in "${IMAGE_TABLE[@]}"; do
        IFS='|' read -r name _ _ memberships required source <<< "$entry"
        case ",$memberships," in *",$scope,"*) ;; *) continue ;; esac
        if [ "$source" = "built" ]; then kind="nodalarc"; else kind="external"; fi
        emit_record "$scope" "$kind" "$name" "$(image_for "$name")" "$required" "$source"
    done
}

list_build_images() { list_members build; }
list_platform_runtime_images() { list_members platform; }
list_session_runtime_images() { list_members session; }
list_third_party_runtime_images() { list_members third-party; }
list_optional_images() { list_members optional; }

list_nodalarc_runtime_images() {
    list_platform_runtime_images
    list_session_runtime_images
}

list_all_runtime_images() {
    list_nodalarc_runtime_images
    list_third_party_runtime_images
}

list_platform_resources() {
    # name<TAB>resource<TAB>source for every required entry that runs as a workload
    local entry name resource memberships required source
    for entry in "${IMAGE_TABLE[@]}"; do
        IFS='|' read -r name _ resource memberships required source <<< "$entry"
        [ "$resource" != "-" ] || continue
        [ "$required" = "required" ] || continue
        printf '%s\t%s\t%s\n' "$name" "$resource" "$source"
    done
}

registry_digest_ref() {
    # The registry's content name for a built image: REF@sha256:DIGEST. A
    # registry that does not report the digest fails the command; the tag
    # alone is never substituted.
    local image="$1" without_host repo tag accept digest
    without_host="${image#"$REGISTRY_HOST_RESOLVED"/}"
    if [ -z "$REGISTRY_HOST_RESOLVED" ] || [ "$without_host" = "$image" ]; then
        echo "na-images: $image is not in registry '${REGISTRY_HOST_RESOLVED}'" >&2
        exit 1
    fi
    repo="${without_host%:*}"
    tag="${without_host##*:}"
    accept="application/vnd.oci.image.index.v1+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.docker.distribution.manifest.v2+json"
    digest="$(
        curl -sfI --max-time 10 -H "Accept: $accept" \
            "http://$REGISTRY_HOST_RESOLVED/v2/$repo/manifests/$tag" \
            | tr -d '\r' \
            | awk -F': ' 'tolower($1) == "docker-content-digest" {print $2}'
    )" || true
    if ! [[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]]; then
        echo "na-images: registry $REGISTRY_HOST_RESOLVED reported no content digest for $image" >&2
        exit 1
    fi
    printf '%s@%s\n' "$image" "$digest"
}

session_image_ref() {
    # Session pods name their images by digest, so a node that holds the
    # image starts the container without asking the registry again. In
    # single-node mode the images are loaded into containerd directly and
    # there is no registry to ask.
    local ref
    ref="$(image_for "$1")"
    if [ "$MODE_RESOLVED" = "single-node" ]; then
        printf '%s\n' "$ref"
    else
        registry_digest_ref "$ref"
    fi
}

workload_dev_overrides() {
    # Map the shipped workload profiles' placeholder image references to the
    # tree's real images. Development only: the value reaches the Operator
    # as WORKLOAD_DEV_IMAGE_OVERRIDES and every substitution is logged as
    # non-reproducible. Raw JSON: it must NEVER pass through helm --set
    # parsing, where braces and commas are list syntax. It travels as a
    # generated values file (workload-dev-overrides-values).
    local zeros="0000000000000000000000000000000000000000000000000000000000000000"
    local frr base
    # Command substitutions run without errexit: every failure is checked.
    frr="$(session_image_ref frr)" || exit 1
    base="$(session_image_ref base)" || exit 1
    printf '{"registry.example/nodalarc/frr@sha256:%s":"%s","registry.example/nodalarc/base@sha256:%s":"%s"}' \
        "$zeros" "$frr" "$zeros" "$base"
}

workload_dev_overrides_values() {
    # YAML values fragment carrying the JSON verbatim as one string scalar.
    local overrides
    overrides="$(workload_dev_overrides)" || exit 1
    printf "workloadDevImageOverrides: '%s'\n" "$overrides"
}

helm_image_args() {
    local pull_policy entry name key
    if [ "$MODE_RESOLVED" = "single-node" ]; then
        pull_policy="Never"
    else
        pull_policy="Always"
    fi

    printf '%s\n' "--set-string=buildTag=$TAG"
    printf '%s\n' "--set-string=imagePullPolicy=$pull_policy"
    local memberships ref
    for entry in "${IMAGE_TABLE[@]}"; do
        IFS='|' read -r name key _ memberships _ _ <<< "$entry"
        [ "$key" != "-" ] || continue
        case ",$memberships," in
            *,session,*) ref="$(session_image_ref "$name")" || exit 1 ;;
            *) ref="$(image_for "$name")" || exit 1 ;;
        esac
        printf '%s\n' "--set-string=images.$key=$ref"
    done
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
  session-image-for NAME
  helm-key-for NAME
  resource-for NAME
  list-platform-resources
  helm-image-args
  workload-dev-overrides-values
EOF
}

command="${1:-}"
if [ -z "$command" ]; then
    usage >&2
    exit 2
fi

# Static lookups answer from the table alone; only image references need the
# transport mode (registry host, prefix), which may consult the cluster.
case "$command" in
    helm-key-for|resource-for|list-platform-resources|-h|--help|help) ;;
    *) resolve_mode ;;
esac

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
    session-image-for)
        if [ -z "${2:-}" ]; then
            echo "na-images: session-image-for requires a logical image name" >&2
            exit 2
        fi
        session_image_ref "$2"
        ;;
    helm-key-for)
        if [ -z "${2:-}" ]; then
            echo "na-images: helm-key-for requires a logical image name" >&2
            exit 2
        fi
        helm_key_for "$2"
        ;;
    resource-for)
        if [ -z "${2:-}" ]; then
            echo "na-images: resource-for requires a logical image name" >&2
            exit 2
        fi
        resource_for "$2"
        ;;
    list-platform-resources) list_platform_resources ;;
    helm-image-args) helm_image_args ;;
    workload-dev-overrides-values) workload_dev_overrides_values ;;
    -h|--help|help) usage ;;
    *)
        echo "na-images: unknown command '$command'" >&2
        usage >&2
        exit 2
        ;;
esac
