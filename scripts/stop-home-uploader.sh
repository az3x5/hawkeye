#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
docker compose --file "${repo_dir}/docker-compose.home-uploader.yml" down

