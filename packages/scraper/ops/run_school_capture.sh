#!/usr/bin/env bash
# Daily entry: original per-university scope, never the whole metro inventory.
set -euo pipefail
umask 077
qrent_root=${QRENT_ROOT:-/root/qrent-scraper}
qrent_release=${QRENT_RELEASE_DIR:-$qrent_root/releases/recovery-20261027}
mkdir -p "$qrent_root/logs"
exec 9>"$qrent_root/.capture.lock"
flock -n 9 || exit 75
cd "$qrent_release"
export SCRAPER_ENV_FILE="$qrent_release/.env"
# Both a full scheduled run and explicit recovery retain the original lock.
if [[ $# == 2 && "$1" == --recover-only ]]; then
    qrent_report=$2
elif [[ $# == 0 ]]; then
    qrent_run=$(.venv/bin/python -c 'from datetime import datetime;print(datetime.now().strftime("%Y%m%d_%H%M%S_%f"))')
    qrent_output="$qrent_root/output/school-daily-${qrent_run:0:8}"
    qrent_report="$qrent_output/${qrent_run}_result.json"
    qrent_status=0
    timeout --kill-after=30s 3h xvfb-run -a .venv/bin/python -u recovery_runner.py \
        --run-tag "$qrent_run" --school-scope --cities sydney --scrapers domain realestate \
        --pages 7 --limit 2000 --detail-limit 100 --details-per-area 10 --retries 1 \
        --max-requests 40000 --proxy-bytes 150000000 --db "$qrent_root/properties.db" \
        --output "$qrent_output" \
        2>&1 | tee -a "$qrent_root/logs/school-$(date +%Y%m%d).log" || qrent_status=$?
    # The legacy root also accepts exit 2; never leak tool/argument errors as 2.
    [[ "$qrent_status" == 2 ]] && exit 1
    [[ "$qrent_status" == 0 || "$qrent_status" == 1 ]] || exit "$qrent_status"
else
    echo 'Use the fixed scheduled window or --recover-only ORIGINAL_REPORT' >&2
    exit 64
fi
# Return zero to the existing root trigger only after all 41 queries are proven.
qrent_status=0
.venv/bin/python -u ops/recover_school_capture.py --root "$qrent_root" \
    --capture-result="$qrent_report" --capture-lock-fd 9 \
    2>&1 | tee -a "$qrent_root/logs/school-$(date +%Y%m%d).log" || qrent_status=$?
[[ "$qrent_status" == 2 ]] && exit 1
exit "$qrent_status"
