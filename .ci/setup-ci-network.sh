#!/usr/bin/env bash
set -euo pipefail
sudo apt-get update
sudo apt-get install -y --no-install-recommends wireguard-tools nmap iptables python3-venv
python3 -m venv "$RUNNER_TEMP/ci-network-python"
"$RUNNER_TEMP/ci-network-python/bin/pip" install 'boto3>=1.35,<2'
echo "$RUNNER_TEMP/ci-network-python/bin" >> "$GITHUB_PATH"
