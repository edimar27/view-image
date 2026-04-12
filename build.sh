#!/usr/bin/env bash
# flet build com bundle de CA (Mozilla via certifi).
# Evita SSL: CERTIFICATE_VERIFY_FAILED ao descarregar o Flutter (Python python.org no macOS).
#
# Se o .app parecer com código antigo após alterar main.py:
#   ./build.sh macos --yes --clear-cache
# ou:
#   FLET_BUILD_CLEAR_CACHE=1 ./build.sh macos --yes
#
# O pyproject.toml exclui .venv/ e build/ do zip — não copie a venv para dentro do pacote.
#
# O build usa scripts/flet_build_with_openfile.py para corrigir o main.dart do Flet:
# sem isso, «Abrir com» no macOS passa o caminho da imagem e o template confunde com modo dev.
set -euo pipefail
cd "$(dirname "$0")"
if [[ -f .venv/bin/activate ]]; then
  # shellcheck source=/dev/null
  source .venv/bin/activate
fi
# Homebrew antes do RVM/outros: o Flutter invoca `pod`; se o PATH tiver o gem do RVM
# à frente, aparece «CocoaPods is installed but broken».
if [[ "$(uname -s)" == "Darwin" ]]; then
  _brew_prefix=""
  if [[ -x /opt/homebrew/bin/brew ]]; then
    _brew_prefix="$(/opt/homebrew/bin/brew --prefix 2>/dev/null || true)"
  elif [[ -x /usr/local/bin/brew ]]; then
    _brew_prefix="$(/usr/local/bin/brew --prefix 2>/dev/null || true)"
  fi
  if [[ -n "${_brew_prefix}" ]]; then
    export PATH="${_brew_prefix}/bin:${_brew_prefix}/sbin:${PATH}"
  fi
  # RVM (ex.: JRuby) define GEM_HOME/GEM_PATH; o Ruby do Homebrew mistura com o
  # libexec do CocoaPods e quebra (Gem::MissingSpecError, ex.: minitest).
  unset GEM_HOME GEM_PATH || true
fi
export SSL_CERT_FILE="$(python -c 'import certifi; print(certifi.where())')"
export REQUESTS_CA_BUNDLE="$SSL_CERT_FILE"
extra=()
if [[ "${FLET_BUILD_CLEAR_CACHE:-}" == "1" ]]; then
  extra+=(--clear-cache)
fi
exec python "$(dirname "$0")/scripts/flet_build_with_openfile.py" build "${extra[@]}" "$@"
