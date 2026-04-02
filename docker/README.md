# Docker Infrastructure

This directory contains the self-hosted infrastructure compose stacks used by
the platform.

## Setup

1. Copy `.env.example` to `.env` and fill in real values.
2. Run `./create-networks.sh` once to create the shared external networks.
3. Start the core stack with `docker compose -f compose.yml up -d` from this
   directory, or run the individual stack files directly.
4. Bootstrap Sentry separately from `docker/sentry/` and run the upstream
   `self-hosted` installer there.

## Notes

- `proxy-tier` is used by services that should be reachable through the nginx
  reverse proxy.
- `backend-internal` is used by internal service-to-service traffic.
- Sentry is managed via an external `getsentry/self-hosted` checkout under
  `docker/sentry/`, not through the root compose include list.
