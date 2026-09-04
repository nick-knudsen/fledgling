# OSRM route-network build & deploy runbook

Manual, ordered checklist for building a self-hosted OSRM graph and getting it onto the
VPS. Not automated, not part of `deploy.sh` — this is occasional/one-time-per-region work,
not routine bootstrap, same reasoning as `restrict-firewall-to-cloudflare.sh` staying a
separate manual script instead of folding into deploy.

This reflects the actual North America build (2026-09-02), including what didn't work on
the first pass — worth reading before rebuilding for an OSM data refresh or adding another
continent.

## 1. Provision a temporary build machine — never the VPS

`osrm-extract` needs far more RAM than the finished graph does to serve. Real numbers from
the North America build:

| Machine | Result |
|---|---|
| n2-highmem-16 (16 vCPU / 128GB RAM) | **OOM-killed** ~6 min into "Generating edge-expanded edges," at ~131GB resident, only 10% through that step |
| n2-highmem-32 (32 vCPU / 256GB RAM) | Succeeded — extract peaked at ~183GB, partition ~72GB, customize ~58.5GB |

Start at **256GB RAM minimum** for a continent-sized extract; don't waste a build cycle on
128GB. Machine type is a plain `gcloud compute instances create ... --machine-type=n2-highmem-32`
on Container-Optimized OS (`--image-family=cos-stable --image-project=cos-cloud`) — COS
ships Docker preinstalled, which is all this needs. Boot disk: 500GB is comfortable (source
pbf + full intermediate extract/partition/customize output is ~93GB for North America, plus
working room).

**A brand-new/trial GCP project caps `CPUS_ALL_REGIONS` at 32 globally**, independent of the
per-region quota (which is usually already generous, e.g. 200). A 256GB `n2-highmem-32`
fits exactly at that ceiling; anything bigger needs an explicit quota increase request first
(Console → IAM & Admin → Quotas — not reliably instant even on a paid/upgraded account).
Check `gcloud compute project-info describe --format="value(quotas)" | grep CPUS_ALL_REGIONS`
before sizing the VM.

## 2. Download the source extract

Get the continent/region `.osm.pbf` from Geofabrik. If `download.geofabrik.de` is erroring
(`ERR_READ_ERROR` on the actual binary while `.md5` files still serve fine — happened during
the North America build, seemingly an origin-side issue on their infra, not a network
problem on our end), fall back to the community mirror at
`download.openstreetmap.fr/extracts/` — same file layout, same filenames, plus the same
`.md5` checksums to verify against. **Always verify the checksum before spending hours of
compute on a possibly-corrupt file.**

Run the download as a detached `systemd-run` unit so it survives SSH/tunnel disconnects:

```bash
sudo systemd-run --unit=osrm-dl --collect --property=Type=simple -- /bin/sh -c \
  'curl -sS -L -o /home/<user>/osrm/data/<region>-latest.osm.pbf \
   https://download.openstreetmap.fr/extracts/<region>-latest.osm.pbf \
   2> /home/<user>/osrm/download.log'
```

## 3. Run the pipeline (car profile, MLD)

Same three-step sequence already validated locally against the small Vermont extract in
`data/osrm/`. Run each as its own detached `systemd-run` unit (same pattern as above) so
long steps survive disconnects, and watch memory (`free -h`) alongside the log:

```bash
docker run --rm -v /home/<user>/osrm/data:/data ghcr.io/project-osrm/osrm-backend \
  osrm-extract -p /opt/car.lua /data/<region>-latest.osm.pbf

docker run --rm -v /home/<user>/osrm/data:/data ghcr.io/project-osrm/osrm-backend \
  osrm-partition /data/<region>-latest.osrm

docker run --rm -v /home/<user>/osrm/data:/data ghcr.io/project-osrm/osrm-backend \
  osrm-customize /data/<region>-latest.osrm
```

`osrm-extract` is the only genuinely risky step. Its log has a known-quiet stretch (no new
lines for 10+ minutes during the main OSM parse — that's normal) and one late, memory-heavy
phase, "Generating edge-expanded edges" (u-turn warnings in the log are the signal it's
started) — this is exactly where the 128GB attempt died. Watch memory closely once that
phase starts; each step prints its own `RAM: peak bytes used: <N>` line on completion, which
is the authoritative number for sizing the next attempt if one is needed.

## 4. Figure out which output files are actually needed

`osrm-extract`/`partition`/`customize` leave many intermediate files that `osrm-routed`
never reads. Don't copy the whole `data/` directory to the VPS — check the exact list for
the container's actual version first:

```bash
docker run --rm ghcr.io/project-osrm/osrm-backend osrm-routed --list-inputs --algorithm mld
```

For North America (osrm-backend v26.9.0) this cut the transfer from 93GB down to **~60GB**
by dropping `.osrm.cnbg`, `.osrm.cnbg_to_ebg`, `.osrm.ebg`, `.osrm.enw`,
`.osrm.turn_penalties_index`, `.osrm.restrictions`, and the source `.osm.pbf` itself — none
of which `osrm-routed` opens. Re-run `--list-inputs` for future builds rather than trusting
this list verbatim; it's tied to the exact image version.

## 5. Confirm the VPS actually has room — don't assume

Check real free disk before transferring anything: `ssh <vps> df -h /`. The North America
build's ~60GB requirement did **not** fit in the VPS's stock disk (75GB total, only 37GB
free) even after trimming to the required-only file set — this wasn't a "clean up some
files" problem, the disk was genuinely undersized for a continent-scale graph.

Fix: attach a separate block-storage volume (Hetzner Volumes, DigitalOcean Volumes, etc.)
sized generously (100GB+, to leave room for OSM data growth and future continents), rather
than resizing the root disk. Mount it directly at `/opt/fledgling/data/osrm`:

```bash
mkfs.ext4 -L osrm-data /dev/sdX
mkdir -p /opt/fledgling/data/osrm
mount /dev/disk/by-id/scsi-0<volume-id> /opt/fledgling/data/osrm
chown deploy:deploy /opt/fledgling/data/osrm
echo '/dev/disk/by-id/scsi-0<volume-id> /opt/fledgling/data/osrm ext4 discard,nofail,defaults 0 0' >> /etc/fstab
```

Use the volume's stable `/dev/disk/by-id/...` path in `/etc/fstab`, not the raw `/dev/sdX`
name — device letters aren't guaranteed stable across reboots.

## 6. Transfer the graph to the VPS

Do this **cloud-to-cloud, directly between the build machine and the VPS** — don't relay
through a local laptop/desktop's network connection, which is typically far slower
(especially on upload) than server-to-server bandwidth. For North America this made the
difference between an estimated multi-hour transfer and an actual **~7.5 minutes at
~140MB/s**.

Never copy a real, reusable private key to a temporary cloud VM to make this work. Generate
a single-use ephemeral keypair instead, scoped to just this transfer:

```bash
ssh-keygen -t ed25519 -f /tmp/eph_transfer_key -N "" -C "temp-osrm-transfer"
# append the .pub to the VPS's authorized_keys
# copy only the private half to the build machine
# rsync the required files (see step 4) from the build machine directly to the VPS
# then, immediately after:
#   - remove the line from the VPS's authorized_keys (grep the comment tag, e.g. sed -i '/temp-osrm-transfer/d')
#   - delete the private key from the build machine
#   - delete both local copies
```

After the transfer, fix ownership (rsync as root leaves files root-owned) and verify:

```bash
chown deploy:deploy /opt/fledgling/data/osrm/*
df -h /opt/fledgling/data/osrm
```

## 7. Sequence the code deploy correctly

**Copy the data to the VPS before merging any `docker-compose.yml`/`api.py` change to
`main`.** `deploy.yml` auto-runs `git pull && docker compose up -d --build` on every green
merge — if the `osrm` service definition lands before `<region>-latest.osrm*` actually
exists at `/opt/fledgling/data/osrm/`, the container crash-loops indefinitely
(`restart: unless-stopped` against a missing file). It won't take down the main app, but the
driving filter silently breaks until someone notices.

## 8. Deploy and smoke-test

Either let the next green merge trigger `deploy.yml`, or run
`git pull && docker compose up -d --build` manually once to confirm first. Then:

- An in-region request (real North America center + `max_driving_minutes`) to
  `/api/optimize` returns 200 with a non-empty candidate set.
- An out-of-region request (e.g. a London or Sydney lat/lon) returns 422 with the
  "isn't supported for this location yet" message.
- `docker compose logs osrm` shows a clean startup — graph loaded, no crash loop.

## 9. Tear down the build machine

Delete the VM once the transfer is verified (`gcloud compute instances delete`). If it's a
dedicated throwaway GCP project rather than a shared one, either delete the whole project
(guarantees zero leftover cost) or leave it — an idle project with no running resources
costs nothing, and keeping it around avoids repeating the quota/project-setup steps for the
next region.
