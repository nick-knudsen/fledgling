#!/usr/bin/env bash
# Build OSRM routing artifacts for Fledgling.
#
# Downloads a Geofabrik PBF and runs osrm-extract/partition/customize via the
# osrm/osrm-backend Docker image. Output lands in data/osrm/ and is mounted
# read-only into the osrm service defined in docker-compose.yml.
#
# Usage:   scripts/build_osrm.sh
# Region:  override PBF_URL to swap regions
#          PBF_URL=https://download.geofabrik.de/north-america/us/new-hampshire-latest.osm.pbf scripts/build_osrm.sh
set -euo pipefail

PBF_URL="${PBF_URL:-https://download.geofabrik.de/north-america/us/vermont-latest.osm.pbf}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OSRM_DIR="$REPO_ROOT/data/osrm"
PBF_FILE="$(basename "$PBF_URL")"
OSRM_BASENAME="${PBF_FILE%.osm.pbf}"

mkdir -p "$OSRM_DIR"

if [ ! -f "$OSRM_DIR/$PBF_FILE" ]; then
    echo "Downloading $PBF_URL"
    curl -L --fail -o "$OSRM_DIR/$PBF_FILE" "$PBF_URL"
else
    echo "PBF already present: $OSRM_DIR/$PBF_FILE (delete to force re-download)"
fi

docker run --rm -v "$OSRM_DIR:/data" osrm/osrm-backend \
    osrm-extract -p /opt/car.lua "/data/$PBF_FILE"
docker run --rm -v "$OSRM_DIR:/data" osrm/osrm-backend \
    osrm-partition "/data/$OSRM_BASENAME.osrm"
docker run --rm -v "$OSRM_DIR:/data" osrm/osrm-backend \
    osrm-customize "/data/$OSRM_BASENAME.osrm"

echo
echo "Done. Update docker-compose.yml 'osrm' service command if the basename"
echo "changed; current artifact: data/osrm/$OSRM_BASENAME.osrm"
