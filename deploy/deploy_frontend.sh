#!/bin/bash
# Build PC2's flashcrowd-web against the real API and publish it on fc-core (nginx root /var/www/flashcrowd).
#   deploy/deploy_frontend.sh <path-to-flashcrowd-web>
set -euo pipefail
SRC="$1"; HOST=ubuntu@18.132.176.215; KEY="$(dirname "$0")/../secrets/fc_ssh"; B=$(mktemp -d)
tar -C "$SRC" --exclude=node_modules --exclude=dist --exclude=.env.local --exclude='preview-*.png' -cf - . | tar -xf - -C "$B"
(cd "$B" && npm install --no-audit --no-fund --loglevel=error && VITE_API_BASE_URL=/api VITE_SYNTHETIC_DATA=false VITE_AGENT_ENABLED=false npx vite build)
find "$B/dist" -name .DS_Store -delete
rsync -az --delete --chmod=D755,F644 --rsync-path="sudo rsync" -e "ssh -i $KEY" "$B/dist/" "$HOST:/var/www/flashcrowd/"
echo "deployed: http://18.132.176.215/"
