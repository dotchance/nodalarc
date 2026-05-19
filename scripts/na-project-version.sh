#!/usr/bin/env bash
# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
#
# Print the NodalArc build/runtime version.
#
# Precedence:
#   1. NODALARC_VERSION, when explicitly provided by release automation.
#   2. The nearest git tag, supporting both "0.4.2" and "nodalarc-0.4.2".
#   3. A deterministic unknown value when no tag/source metadata is available.

set -euo pipefail

if [[ -n "${NODALARC_VERSION:-}" ]]; then
    printf '%s\n' "$NODALARC_VERSION"
    exit 0
fi

format_describe() {
    local described="$1"
    local dirty=""

    described="${described#nodalarc-}"

    if [[ "$described" == *-dirty ]]; then
        dirty=".dirty"
        described="${described%-dirty}"
    fi

    if [[ "$described" =~ ^[0-9]+([.][0-9A-Za-z]+)*$ ]]; then
        if [[ -n "$dirty" ]]; then
            printf '%s+dirty\n' "$described"
        else
            printf '%s\n' "$described"
        fi
        return 0
    fi

    if [[ "$described" =~ ^(.+)-([0-9]+)-g([0-9a-f]+)$ ]]; then
        printf '%s+%s.g%s%s\n' "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}" "${BASH_REMATCH[3]}" "$dirty"
        return 0
    fi

    if [[ "$described" =~ ^[0-9a-f]{7,40}$ ]]; then
        printf '0+g%s%s\n' "$described" "$dirty"
        return 0
    fi

    return 1
}

if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    described="$(
        git describe --tags \
            --match 'nodalarc-[0-9]*' \
            --match '[0-9]*' \
            --dirty \
            --always 2>/dev/null || true
    )"

    if [[ -n "$described" ]] && format_describe "$described"; then
        exit 0
    fi
fi

printf '0+unknown\n'
