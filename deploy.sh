#!/usr/bin/env bash
set -euo pipefail

# ── 1. System packages ──────────────────────────────────────────────
apt-get update
apt-get install -y docker.io docker-compose-v2 nginx certbot python3-certbot-nginx ufw

# ── 2. Firewall ─────────────────────────────────────────────────────
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw --force enable

# ── 3. Clone repo & set env var ─────────────────────────────────────
if [ ! -d /opt/fledgling ]; then
    git clone https://github.com/nick-knudsen/fledgling.git /opt/fledgling
fi
cd /opt/fledgling

if [ ! -f .env ]; then
    echo "EBIRD_API_KEY=your_key_here" > .env
    echo ">>> Edit /opt/fledgling/.env with your real eBird API key <<<"
fi

# ── 4. Build & start the app ────────────────────────────────────────
docker compose up -d --build

# ── 5. Nginx config ─────────────────────────────────────────────────
cp nginx.conf /etc/nginx/sites-available/fledgling
ln -sf /etc/nginx/sites-available/fledgling /etc/nginx/sites-enabled/fledgling
rm -f /etc/nginx/sites-enabled/default
cp cloudflare-realip.conf /etc/nginx/conf.d/cloudflare-realip.conf
nginx -t
systemctl reload nginx

# ── 6. TLS certificate ──────────────────────────────────────────────
certbot --nginx -d fledgli.ng --non-interactive --agree-tos --email nick.knudsen14@gmail.com

echo ""
echo "=== Deployment complete ==="
echo "App running at https://fledgli.ng"
echo ""
echo "Reminders:"
echo "  - Copy your data/ directory to /opt/fledgling/data/"
echo "  - Edit /opt/fledgling/.env with your real EBIRD_API_KEY"
echo "  - Run 'docker compose up -d' after making changes"
echo "  - If Cloudflare is in front of this domain and its zone is already"
echo "    active, run restrict-firewall-to-cloudflare.sh once DNS/TLS are"
echo "    confirmed working. Do NOT run it before that (breaks certbot"
echo "    above and cuts off direct access) - see that script's header."
