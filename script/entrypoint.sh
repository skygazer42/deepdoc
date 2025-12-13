#!/bin/sh
set -e

echo "[DeepDoc] ensuring models exist at: ${MODEL_BASE_DIR:-/app/resources/models}"
python script/download_models.py || echo "[DeepDoc] model pre-download failed, will rely on lazy loading"

exec python api_service.py
