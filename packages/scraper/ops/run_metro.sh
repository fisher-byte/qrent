#!/usr/bin/env bash
set -euo pipefail
umask 077
qrent_root=${QRENT_ROOT:-/root/qrent-scraper}
qrent_release=${QRENT_RELEASE_DIR:-$qrent_root/releases/recovery-20261027}
qrent_output="$qrent_root/output/metro-daily-$(date +%Y%m%d)"
mkdir -p "$qrent_root/logs" "$qrent_output"
cd "$qrent_release"
export SCRAPER_ENV_FILE="$qrent_release/.env"
# metro_runner owns .capture.lock and returns 75 when another campaign is live.
timeout --kill-after=30s 18h xvfb-run -a .venv/bin/python metro_runner.py \
    --mode auto --cities sydney melbourne --scrapers domain realestate \
    --db "$qrent_root/properties.db" --output "$qrent_output" \
    --proxy-bytes 300000000 --max-requests 500000 --detail-limit 0 \
    2>&1 | tee -a "$qrent_root/logs/metro-$(date +%Y%m%d).log"
