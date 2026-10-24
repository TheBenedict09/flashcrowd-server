#!/bin/bash
# Build flashcrowd-arcade and publish it at http://18.132.176.215/arcade/ (separate root: /var/www/arcade).
#   deploy/deploy_arcade.sh <path-to-flashcrowd-arcade>
set -euo pipefail
SRC="$(cd "$1" && pwd)"; HOST=ubuntu@18.132.176.215; KEY="$(cd "$(dirname "$0")/.." && pwd)/secrets/fc_ssh"; B=$(mktemp -d)
tar -C "$SRC" --exclude=node_modules --exclude=dist -cf - . | tar -xf - -C "$B"
(cd "$B" && npm install --no-audit --no-fund --loglevel=error && npx vite build)
find "$B/dist" -name .DS_Store -delete
rsync -az --delete --chmod=D755,F644 --rsync-path="sudo rsync" -e "ssh -i $KEY" "$B/dist/" "$HOST:/var/www/arcade/"
echo "deployed: http://18.132.176.215/arcade/"
