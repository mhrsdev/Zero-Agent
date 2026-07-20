#!/usr/bin/env bash
set -euo pipefail
ROOT=/root/zero
UNIT=zero-listener.service
DROPIN=/etc/systemd/system/${UNIT}.d/memory-v2.conf
mode=${1:---dry-run}
if [[ $mode != --dry-run && $mode != --apply ]]; then echo 'usage: rollback_zero_memory_to_v1.sh [--dry-run|--apply]'; exit 2; fi
cat <<'PLAN'
rollback plan: stop zero-listener.service; set V2 disabled/shadow disabled; restart service.
V2 data is preserved. No database restore occurs without an explicit operator action.
PLAN
[[ $mode == --dry-run ]] && exit 0
systemctl stop "$UNIT"
install -d -m 755 "$(dirname "$DROPIN")"
cat >"$DROPIN" <<'EOF'
[Service]
Environment=ZERO_MEMORY_V2_ENABLED=false
Environment=ZERO_MEMORY_V2_SHADOW=false
Environment=ZERO_MEMORY_V2_READ_ENABLED=false
Environment=ZERO_MEMORY_V2_WRITE_ENABLED=false
EOF
systemctl daemon-reload
systemctl start "$UNIT"
systemctl is-active --quiet "$UNIT"
echo 'rollback applied: V1 runtime path active; V2 DB preserved.'
