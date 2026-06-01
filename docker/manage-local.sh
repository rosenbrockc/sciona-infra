#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$ROOT_DIR/.env.local"
WITH_SENTRY=0
BOOTSTRAP_SENTRY=0
INSTALL_SENTRY=0
SENTRY_CHECKOUT="$ROOT_DIR/sentry/self-hosted"
PASSTHROUGH=()
CORE_STACKS=(authentik temporal opa telemetry)

usage() {
  cat <<USAGE
Usage:
  $(basename "$0") <up|down|ps|logs|config> [options] [-- extra-docker-compose-args]

Options:
  --env-file PATH         Use a specific env file (default: docker/.env.local)
  --with-sentry           Also manage the local Sentry self-hosted stack
  --bootstrap-sentry      Clone/update docker/sentry/self-hosted before use
  --install-sentry        Run upstream ./install.sh before starting Sentry
  --sentry-checkout PATH  Override the Sentry checkout path
  -h, --help              Show this help text

Examples:
  ./manage-local.sh up
  ./manage-local.sh up --with-sentry --bootstrap-sentry --install-sentry
  ./manage-local.sh down --with-sentry -- --volumes
USAGE
}

stack_file() {
  case "$1" in
    authentik) printf '%s' "$ROOT_DIR/authentik/compose.yml" ;;
    temporal) printf '%s' "$ROOT_DIR/temporal/compose.yml" ;;
    opa) printf '%s' "$ROOT_DIR/opa/compose.yml" ;;
    telemetry) printf '%s' "$ROOT_DIR/telemetry/compose.yml" ;;
    *) echo "Unknown stack: $1" >&2; exit 1 ;;
  esac
}

stack_project() {
  case "$1" in
    authentik) printf '%s' "sciona-authentik-local" ;;
    temporal) printf '%s' "sciona-temporal-local" ;;
    opa) printf '%s' "sciona-opa-local" ;;
    telemetry) printf '%s' "sciona-telemetry-local" ;;
    *) echo "Unknown stack: $1" >&2; exit 1 ;;
  esac
}

stack_override() {
  case "$1" in
    authentik) printf '%s' "$ROOT_DIR/authentik/compose.local.override.yml" ;;
    *) printf '%s' "" ;;
  esac
}

if [[ $# -lt 1 ]]; then
  usage
  exit 1
fi

if [[ "$1" == "-h" || "$1" == "--help" ]]; then
  usage
  exit 0
fi

COMMAND="$1"
shift

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file)
      ENV_FILE="$2"
      shift 2
      ;;
    --with-sentry)
      WITH_SENTRY=1
      shift
      ;;
    --bootstrap-sentry)
      BOOTSTRAP_SENTRY=1
      shift
      ;;
    --install-sentry)
      INSTALL_SENTRY=1
      shift
      ;;
    --sentry-checkout)
      SENTRY_CHECKOUT="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      PASSTHROUGH=("$@")
      break
      ;;
    *)
      PASSTHROUGH+=("$1")
      shift
      ;;
  esac
done

ensure_env_file() {
  if [[ ! -f "$ENV_FILE" ]]; then
    cp "$ROOT_DIR/.env.local.example" "$ENV_FILE"
    echo "Created $ENV_FILE from .env.local.example"
  fi
}

load_env_defaults() {
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a

  SCIONA_PROXY_TIER_NETWORK="${SCIONA_PROXY_TIER_NETWORK:-proxy-tier}"
  SCIONA_BACKEND_NETWORK="${SCIONA_BACKEND_NETWORK:-backend-internal}"
}

ensure_network() {
  local network_name="$1"
  if ! docker network inspect "$network_name" >/dev/null 2>&1; then
    docker network create "$network_name" >/dev/null
    echo "Created docker network: $network_name"
  fi
}

ensure_local_networks() {
  load_env_defaults
  ensure_network "$SCIONA_PROXY_TIER_NETWORK"
  ensure_network "$SCIONA_BACKEND_NETWORK"
}

run_stack_compose() {
  local stack="$1"
  shift

  local file
  local project
  local override
  file="$(stack_file "$stack")"
  project="$(stack_project "$stack")"
  override="$(stack_override "$stack")"

  if [[ -n "$override" && -f "$override" ]]; then
    docker compose --env-file "$ENV_FILE" -p "$project" -f "$file" -f "$override" "$@"
    return
  fi

  docker compose --env-file "$ENV_FILE" -p "$project" -f "$file" "$@"
}

run_core_stacks() {
  local stack
  for stack in "${CORE_STACKS[@]}"; do
    if [[ ${#PASSTHROUGH[@]} -gt 0 ]]; then
      run_stack_compose "$stack" "$COMMAND" "${PASSTHROUGH[@]}"
    else
      run_stack_compose "$stack" "$COMMAND"
    fi
  done
}

run_core_stacks_reverse() {
  local idx
  for (( idx=${#CORE_STACKS[@]}-1; idx>=0; idx-- )); do
    if [[ ${#PASSTHROUGH[@]} -gt 0 ]]; then
      run_stack_compose "${CORE_STACKS[$idx]}" "$COMMAND" "${PASSTHROUGH[@]}"
    else
      run_stack_compose "${CORE_STACKS[$idx]}" "$COMMAND"
    fi
  done
}

sentry_compose_file() {
  if [[ -f "$SENTRY_CHECKOUT/docker-compose.yml" ]]; then
    printf '%s' "$SENTRY_CHECKOUT/docker-compose.yml"
    return
  fi
  if [[ -f "$SENTRY_CHECKOUT/compose.yml" ]]; then
    printf '%s' "$SENTRY_CHECKOUT/compose.yml"
    return
  fi
  echo "Unable to find Sentry compose file under $SENTRY_CHECKOUT" >&2
  exit 1
}

ensure_sentry_checkout() {
  if [[ $BOOTSTRAP_SENTRY -eq 1 || ! -d "$SENTRY_CHECKOUT/.git" ]]; then
    "$ROOT_DIR/sentry/bootstrap.sh" "$SENTRY_CHECKOUT"
  fi

  mkdir -p "$SENTRY_CHECKOUT"
  if [[ ! -f "$SENTRY_CHECKOUT/.env.custom" ]]; then
    cp "$ROOT_DIR/sentry/.env.custom.local.example" "$SENTRY_CHECKOUT/.env.custom"
    echo "Created $SENTRY_CHECKOUT/.env.custom from .env.custom.local.example"
  elif cmp -s "$SENTRY_CHECKOUT/.env.custom" "$ROOT_DIR/sentry/.env.custom.example"; then
    cp "$ROOT_DIR/sentry/.env.custom.local.example" "$SENTRY_CHECKOUT/.env.custom"
    echo "Replaced $SENTRY_CHECKOUT/.env.custom with .env.custom.local.example"
  fi
}

sentry_install_shell() {
  if [[ -n "${SCIONA_SENTRY_BASH:-}" ]]; then
    printf '%s' "$SCIONA_SENTRY_BASH"
    return
  fi
  if [[ -x /opt/homebrew/bin/bash ]]; then
    printf '%s' /opt/homebrew/bin/bash
    return
  fi
  printf '%s' bash
}

ensure_sentry_installed() {
  if [[ ! -f "$SENTRY_CHECKOUT/.env" || ! -f "$SENTRY_CHECKOUT/sentry/config.yml" ]]; then
    if [[ $INSTALL_SENTRY -ne 1 ]]; then
      cat >&2 <<MSG
Sentry is not installed in $SENTRY_CHECKOUT.
Re-run with --install-sentry to execute the upstream installer.
MSG
      exit 1
    fi
    (
      cd "$SENTRY_CHECKOUT"
      REPORT_SELF_HOSTED_ISSUES=0 "$(sentry_install_shell)" ./install.sh --skip-user-creation
    )
  elif [[ $INSTALL_SENTRY -eq 1 ]]; then
    (
      cd "$SENTRY_CHECKOUT"
      REPORT_SELF_HOSTED_ISSUES=0 "$(sentry_install_shell)" ./install.sh --skip-user-creation
    )
  fi
}

run_sentry_compose() {
  local compose_file
  compose_file="$(sentry_compose_file)"
  (
    cd "$SENTRY_CHECKOUT"
    set -a
    # shellcheck disable=SC1091
    source ./.env
    # shellcheck disable=SC1091
    source ./.env.custom
    set +a
    docker compose -f "$compose_file" "$@"
  )
}

handle_sentry() {
  if [[ $WITH_SENTRY -ne 1 ]]; then
    return
  fi

  ensure_sentry_checkout

  case "$COMMAND" in
    up)
      ensure_sentry_installed
      if [[ ${#PASSTHROUGH[@]} -gt 0 ]]; then
        run_sentry_compose up -d "${PASSTHROUGH[@]}"
      else
        run_sentry_compose up -d
      fi
      ;;
    down)
      if [[ -d "$SENTRY_CHECKOUT" ]]; then
        if [[ ${#PASSTHROUGH[@]} -gt 0 ]]; then
          run_sentry_compose down "${PASSTHROUGH[@]}"
        else
          run_sentry_compose down
        fi
      fi
      ;;
    ps|logs|config)
      ensure_sentry_installed
      if [[ ${#PASSTHROUGH[@]} -gt 0 ]]; then
        run_sentry_compose "$COMMAND" "${PASSTHROUGH[@]}"
      else
        run_sentry_compose "$COMMAND"
      fi
      ;;
    *)
      echo "Unsupported command for Sentry: $COMMAND" >&2
      exit 1
      ;;
  esac
}

ensure_env_file
ensure_local_networks

case "$COMMAND" in
  up)
    if [[ ${#PASSTHROUGH[@]} -eq 0 ]]; then
      PASSTHROUGH=(--detach)
    fi
    run_core_stacks
    handle_sentry
    ;;
  down)
    handle_sentry
    run_core_stacks_reverse
    ;;
  ps|logs|config)
    run_core_stacks
    if [[ $WITH_SENTRY -eq 1 ]]; then
      handle_sentry
    fi
    ;;
  *)
    echo "Unsupported command: $COMMAND" >&2
    usage
    exit 1
    ;;
esac
