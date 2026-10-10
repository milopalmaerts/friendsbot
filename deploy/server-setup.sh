#!/bin/bash
# One-shot setup for a fresh Ubuntu server (Oracle Cloud "Always Free", Hetzner, ...).
# Paste this whole file into "Cloud-init script" when creating the instance, or run it once:
#   curl -fsSL https://raw.githubusercontent.com/milopalmaerts/friendsbot/claude/gold-trading-bot-asia-k5fc92/deploy/server-setup.sh | sudo bash
# Afterwards, set the API key once (it is asked for, never echoed):
#   sudo friendsbot-key
set -euo pipefail
REPO=https://github.com/milopalmaerts/friendsbot.git
BRANCH=claude/gold-trading-bot-asia-k5fc92

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y git python3 python3-venv python3-pip

id friendsbot >/dev/null 2>&1 || useradd --system --create-home --home-dir /opt/friendsbot --shell /usr/sbin/nologin friendsbot
if [ ! -d /opt/friendsbot/.git ]; then
  rm -rf /opt/friendsbot/* 2>/dev/null || true
  sudo -u friendsbot git clone --branch "$BRANCH" "$REPO" /opt/friendsbot
fi
sudo -u friendsbot python3 -m venv /opt/friendsbot/.venv
sudo -u friendsbot /opt/friendsbot/.venv/bin/pip install --quiet -r /opt/friendsbot/requirements.txt

mkdir -p /etc/friendsbot
touch /etc/friendsbot/env
chmod 600 /etc/friendsbot/env
cp /opt/friendsbot/deploy/friendsbot.service /etc/systemd/system/friendsbot.service

cat > /usr/local/bin/friendsbot-key <<'KEY'
#!/bin/bash
# Store the MyFundedPerps API key and (re)start the bot.
set -euo pipefail
read -r -s -p "MyFundedPerps API key (fp_test_...): " key; echo
case "$key" in fp_test_*|fp_live_*) ;; *) echo "Dit lijkt geen MyFundedPerps-sleutel."; exit 1;; esac
printf 'FP_API_KEY=%s\n' "$key" > /etc/friendsbot/env
chmod 600 /etc/friendsbot/env
systemctl daemon-reload
systemctl enable --now friendsbot
systemctl restart friendsbot
echo "Bot gestart. Volg hem met: journalctl -u friendsbot -f"
KEY
chmod 755 /usr/local/bin/friendsbot-key

systemctl daemon-reload
echo "Klaar. Zet nu de sleutel met: sudo friendsbot-key"
