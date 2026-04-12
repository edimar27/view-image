#!/usr/bin/env bash
# Arranque do Flet com bundle de CA (Mozilla via certifi).
# Necessário quando o Python do python.org não tem cert.pem em etc/openssl/
# (erro: SSL: CERTIFICATE_VERIFY_FAILED).
set -euo pipefail
cd "$(dirname "$0")"
if [[ -f .venv/bin/activate ]]; then
  # shellcheck source=/dev/null
  source .venv/bin/activate
fi
export SSL_CERT_FILE="$(python -c 'import certifi; print(certifi.where())')"
export REQUESTS_CA_BUNDLE="$SSL_CERT_FILE"
exec flet run main.py "$@"
