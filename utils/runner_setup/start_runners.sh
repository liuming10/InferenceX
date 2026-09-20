#!/bin/bash

set -e

if [ $# -lt 3 ] || [ $# -gt 5 ]; then
  echo "Usage: $0 <START_INDEX> <END_INDEX> <BASE_DIR> [SESSION_NAME] [ARCHIVE_DIR]"
  echo "Example: $0 0 9 /path/to/base-dir my-session /path/to/action-archives"
  exit 1
fi

START="$1"
END="$2"
BASE_DIR="$3"
SESSION="${4:-github-actions}"
ARCHIVE_DIR="${5:-${BASE_DIR}/arch}"
PRESEED_SCRIPT="$(cd "$(dirname "$0")" && pwd -P)/preseed_actions.sh"

if [ ! -f "$PRESEED_SCRIPT" ]; then
  echo "Action cache preseed script is missing: $PRESEED_SCRIPT" >&2
  exit 1
fi

# Kill existing session if it exists
tmux kill-session -t "$SESSION" 2>/dev/null || true

# Create session with the first runner
PADDED_START=$(printf "%02d" "$START")
tmux new-session -d -s "$SESSION" -n "runners"
tmux send-keys -t "$SESSION" "bash '$PRESEED_SCRIPT' '${BASE_DIR}/gharunner${PADDED_START}/actions-runner' '$ARCHIVE_DIR' && cd '${BASE_DIR}/gharunner${PADDED_START}/actions-runner' && ./run.sh" Enter

# Create additional panes for the rest
for i in $(seq $((START + 1)) "$END"); do
  PADDED=$(printf "%02d" "$i")
  tmux split-window -t "$SESSION"
  tmux send-keys -t "$SESSION" "bash '$PRESEED_SCRIPT' '${BASE_DIR}/gharunner${PADDED}/actions-runner' '$ARCHIVE_DIR' && cd '${BASE_DIR}/gharunner${PADDED}/actions-runner' && ./run.sh" Enter
  tmux select-layout -t "$SESSION" tiled
done
