#!/usr/bin/env bash
# Deploys the intake receiver. Run from Cloud Shell: bash functions/deploy.sh
set -euo pipefail
cd "$(dirname "$0")/intake"
gcloud functions deploy webhook-intake-save_answers \
  --gen2 --region us-west1 --runtime python312 \
  --source . --entry-point intake --trigger-http --allow-unauthenticated \
  --memory 256Mi --timeout 30s \
  --set-secrets ODOO_URL=ODOO_URL:latest,ODOO_DB=ODOO_DB:latest,ODOO_USER=ODOO_USER:latest,ODOO_API_KEY=ODOO_API_KEY:latest \
  --quiet 2>&1 | tail -5
