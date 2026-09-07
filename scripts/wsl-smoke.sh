#!/usr/bin/env bash
# Runs only in the disposable CI distribution; not a workstation installer.
set -euo pipefail
uname -r | grep -qi 'wsl2'
apt-get update -qq
apt-get install -y -qq python3 python3-venv git curl ca-certificates xz-utils
work=$(mktemp -d /tmp/ltk-wsl-XXXXXX)
cd "$work"
curl -fsSLO https://nodejs.org/dist/v22.23.2/node-v22.23.2-linux-x64.tar.xz
curl -fsSLO https://nodejs.org/dist/v22.23.2/SHASUMS256.txt
grep ' node-v22.23.2-linux-x64.tar.xz$' SHASUMS256.txt | sha256sum -c -
tar -xJf node-v22.23.2-linux-x64.tar.xz
export PATH="$work/node-v22.23.2-linux-x64/bin:$PATH"
curl -fsSL https://github.com/RizRiyz/luvus/releases/download/v0.13.4/luvus-v0.13.4-x86_64-unknown-linux-musl.tar.gz -o luvus.tar.gz
echo '5a544c93cdca526d48a52eb40ec46a802459dd385a7d240dc0af5c8dc1df1cdd  luvus.tar.gz' | sha256sum -c -
mkdir native
tar -xzf luvus.tar.gz -C native
export LUVUS_BIN_PATH
LUVUS_BIN_PATH=$(find "$work/native" -type f -name luvus -print -quit)
export GITHUB_SHA="$1"
git clone -q https://github.com/kaceper11/luvus-toolkit.git toolkit
cd toolkit
git checkout -q "$GITHUB_SHA"
python3 toolkit.py bootstrap
node scripts/power-smoke.mjs
python3 scripts/smoke.py --install
python3 toolkit.py test
npm test
