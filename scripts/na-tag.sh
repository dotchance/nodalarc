#!/usr/bin/env bash
# The single source of image-tag truth.
#
# Image identity must equal content identity: two different trees must never
# share a tag, or the registry serves stale code under a current name and
# deploys race each other (this happened; it cost hours).
#
#   clean tree  -> <short git sha>                e.g. b64999c4
#   dirty tree  -> <short git sha>-<diff hash>    e.g. b64999c4-3f9a01c2
#
# The dirty hash covers staged + unstaged tracked changes AND the names and
# contents of untracked, non-ignored files (new source files change builds
# too). Every distinct tree state therefore produces a distinct, immutable tag.

set -euo pipefail

sha="$(git rev-parse --short=8 HEAD)"

# Porcelain output covers file names, the diff tracked content, and the hash
# list the contents of untracked, non-ignored files.
status="$(git status --porcelain=v1)"
tracked="$(git diff HEAD)"
untracked="$(git ls-files --others --exclude-standard -z | xargs -0 -r sha256sum --)"
dirty_input="${status}${tracked}${untracked}"

if [ -z "$dirty_input" ]; then
    echo "$sha"
else
    dirty_hash="$(printf '%s' "$dirty_input" | sha256sum | cut -c1-8)"
    echo "${sha}-${dirty_hash}"
fi
