#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# metals-satellite-monitor pipeline
#
#   bash run.sh                  full live pipeline (02 pull -> 06 report); needs EE_PROJECT in .env
#   bash run.sh --demo           same pipeline on synthetic data in ./demo_out (no Earth Engine)
#   bash run.sh --check-sites    step 01 only: thumbnails + GeoJSON to verify every site ROI
#   bash run.sh --from 03        re-run from a given step using the cached pull
#
# Any other flag is passed to every step, e.g.
#   bash run.sh --commodities copper,nickel --start 2020-01-01 --prices-csv my_prices.csv
# -----------------------------------------------------------------------------
set -euo pipefail
cd "$(dirname "$0")"

if [ -f .env ]; then set -a; . ./.env; set +a; fi
export MM_ROOT="$(pwd)"
export PYTHONPATH="$(pwd)/src${PYTHONPATH:+:$PYTHONPATH}"
PY="${PYTHON:-python}"

FROM="02"
ONLY=""
ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --from) FROM="$2"; shift 2 ;;
    --check-sites) ONLY="01"; shift ;;
    *) ARGS+=("$1"); shift ;;
  esac
done

for script in pipeline/0*.py; do
  n="$(basename "$script" | cut -c1-2)"
  if [ -n "$ONLY" ]; then
    [ "$n" = "$ONLY" ] || continue
  else
    [ "$n" = "01" ] && continue
    [[ "$n" < "$FROM" ]] && continue
  fi
  echo "==> $script"
  "$PY" "$script" ${ARGS[@]+"${ARGS[@]}"}
done
echo "Done."
