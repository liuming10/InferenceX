#!/usr/bin/env bash
# Populate one self-hosted runner's persistent cache from host-local Action archives.

set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
    echo "Usage: $0 <ACTIONS_RUNNER_DIR> [ARCHIVE_DIR]" >&2
    echo "Example: $0 /stortest/lium_space/agentX/actions-runner" >&2
    exit 2
fi

RUNNER_DIR=$(cd "$1" && pwd -P)
ARCHIVE_DIR=${2:-"$RUNNER_DIR/arch"}
ARCHIVE_DIR=$(cd "$ARCHIVE_DIR" && pwd -P)
ACTIONS_DIR="$RUNNER_DIR/_work/_actions/actions"

if [[ ! -f "$RUNNER_DIR/run.sh" ]]; then
    echo "Not an actions-runner directory: $RUNNER_DIR" >&2
    exit 2
fi

find_archive() {
    local name=$1
    local sha=$2
    local -a matches=()
    local archive

    shopt -s nullglob
    for archive in "$ARCHIVE_DIR"/*"$sha"*.tar "$ARCHIVE_DIR"/*"$sha"*.tar.gz "$ARCHIVE_DIR"/*"$sha"*.tgz "$ARCHIVE_DIR"/*"$sha"*.zip; do
        matches+=("$archive")
    done
    if (( ${#matches[@]} == 0 )); then
        for archive in "$ARCHIVE_DIR"/*"$name"*.tar "$ARCHIVE_DIR"/*"$name"*.tar.gz "$ARCHIVE_DIR"/*"$name"*.tgz "$ARCHIVE_DIR"/*"$name"*.zip; do
            matches+=("$archive")
        done
    fi
    shopt -u nullglob

    if (( ${#matches[@]} != 1 )); then
        echo "Expected exactly one $name archive under $ARCHIVE_DIR; found ${#matches[@]}." >&2
        exit 1
    fi
    printf '%s\n' "${matches[0]}"
}

extract_action() {
    local archive=$1
    local destination=$2
    local temporary
    local action_root

    temporary=$(mktemp -d "${TMPDIR:-/tmp}/inferencex-action-cache.XXXXXX")
    trap 'rm -rf "$temporary"' RETURN
    case "$archive" in
        *.tar) tar --no-same-owner -xf "$archive" -C "$temporary" ;;
        *.tar.gz|*.tgz) tar --no-same-owner -xzf "$archive" -C "$temporary" ;;
        *.zip) unzip -q "$archive" -d "$temporary" ;;
        *) echo "Unsupported action archive format: $archive" >&2; return 1 ;;
    esac

    action_root=$(find "$temporary" -type f -name action.yml -printf '%h\n' | sort -u)
    if [[ $(printf '%s\n' "$action_root" | sed '/^$/d' | wc -l) -ne 1 ]]; then
        echo "Archive must contain exactly one action.yml: $archive" >&2
        return 1
    fi
    action_root=${action_root//$'\n'/}
    if [[ ! -d "$action_root/dist" ]]; then
        echo "Action archive has no compiled dist directory: $archive" >&2
        return 1
    fi

    mkdir -p "$(dirname "$destination")"
    mv "$action_root" "$destination"
    trap - RETURN
    rm -rf "$temporary"
}

preseed_action() {
    local repository=$1
    local sha=$2
    local entrypoint=$3
    local destination="$ACTIONS_DIR/$repository/$sha"
    local archive

    if [[ -f "$destination/action.yml" && -f "$destination/$entrypoint" ]]; then
        if [[ ! -f "$destination.completed" ]]; then
            date --utc +%FT%TZ > "$destination.completed"
            echo "Repaired action cache watermark: $repository@$sha"
        else
            echo "Action cache hit: $repository@$sha"
        fi
        return
    fi
    if [[ -e "$destination" ]]; then
        echo "Incomplete action cache entry: $destination" >&2
        exit 1
    fi

    archive=$(find_archive "$repository" "$sha")
    echo "Installing host-local action: $repository@$sha from $archive"
    extract_action "$archive" "$destination"
    if [[ ! -f "$destination/action.yml" || ! -f "$destination/$entrypoint" ]]; then
        echo "Installed action cache entry is incomplete: $destination" >&2
        exit 1
    fi
    date --utc +%FT%TZ > "$destination.completed"
}

preseed_action "checkout" "3d3c42e5aac5ba805825da76410c181273ba90b1" "dist/index.js"
preseed_action "upload-artifact" "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a" "dist/upload/index.js"
