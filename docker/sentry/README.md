# Self-Hosted Sentry

This directory contains the local glue for running the upstream
`getsentry/self-hosted` stack alongside the rest of the platform infra without
vendoring the full upstream repository into this repo.

## What lives here

- `bootstrap.sh` clones or updates the upstream checkout and installs the local
  override/template files.
- `docker-compose.override.yml` is copied into the upstream checkout so the
  Sentry nginx container joins the shared `proxy-tier` network.
- `.env.custom.example` is copied into the upstream checkout as `.env.custom`
  on first bootstrap.

## Workflow

1. Create the shared networks once:

   ```bash
   cd docker
   ./create-networks.sh
   ```

2. Bootstrap the upstream checkout:

   ```bash
   cd docker/sentry
   ./bootstrap.sh
   ```

3. Review and edit `self-hosted/.env.custom` for your hostname, mail settings,
   and any upstream Sentry tuning.

4. Run the upstream installer:

   ```bash
   cd docker/sentry/self-hosted
   ./install.sh
   ```

5. Start Sentry:

   ```bash
   docker compose up -d
   ```

## Notes

- The root [docker/compose.yml](/Users/conrad/personal/ageo-matcher/docker/compose.yml)
  intentionally does not include Sentry. The upstream stack is managed
  separately because it owns a large set of services and installation scripts.
- The override file routes public traffic through the shared nginx proxy rather
  than exposing Sentry directly on ports `80`/`443`.
