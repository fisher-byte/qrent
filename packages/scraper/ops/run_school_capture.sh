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
# Match the old seven-page per-area window. This is not a claim of full
# market coverage. Models, commute calls and production publishing are separate.
timeout --kill-after=30s 3h xvfb-run -a .venv/bin/python -u recovery_runner.py \
    --school-scope --cities sydney --scrapers domain realestate \
    --pages 7 --limit 2000 --no-details --retries 1 \
    --max-requests 40000 --proxy-bytes 150000000 --db "$qrent_root/properties.db" \
    --output "$qrent_root/output/school-daily-$(date +%Y%m%d)" "$@" \
    2>&1 | tee -a "$qrent_root/logs/school-$(date +%Y%m%d).log"
