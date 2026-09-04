#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cyber_ai_api="${CYBER_AI_API_URL:-http://100.74.113.94:8000}"
uploader_port="${HOME_UPLOADER_PORT:-3100}"

if [[ "${cyber_ai_api}" == http://api:* ]]; then
  printf 'Using the laptop API through EagleEye internal networking.\n'
elif curl --connect-timeout 3 --max-time 5 --fail --silent \
  "${cyber_ai_api}/api/v1/health" >/dev/null; then
  printf 'Cyber-ai API is reachable at %s.\n' "${cyber_ai_api}"
else
  printf 'Warning: cyber-ai is currently unreachable at %s.\n' "${cyber_ai_api}" >&2
  printf 'The uploader will start, but sign-in and uploads will fail until cyber-ai returns.\n' >&2
fi

CYBER_AI_API_URL="${cyber_ai_api}" HOME_UPLOADER_PORT="${uploader_port}" \
  docker compose --file "${repo_dir}/docker-compose.home-uploader.yml" up --detach --build

printf 'Home uploader: http://127.0.0.1:%s/sign-in\n' "${uploader_port}"
