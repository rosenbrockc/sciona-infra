# Docker Infrastructure

This directory contains the self-hosted infrastructure compose stacks used by
`sciona-infra`.

## Production-style setup

1. Copy `.env.example` to `.env` and fill in real values.
2. Run `./create-networks.sh` once to create the shared external networks.
3. Start the core stack with `docker compose -f compose.yml up -d` from this
   directory, or run the individual stack files directly.
4. Bootstrap Sentry separately from `docker/sentry/` and run the upstream
   `self-hosted` installer there.

## Local no-TLS setup

Use the helper script when you want the infra pieces reachable directly on
`localhost` without nginx-proxy, ACME, or real DNS.

1. Copy `.env.local.example` to `.env.local` and adjust values if needed.
2. Start the local infra stack:

   ```bash
   cd docker
   ./manage-local.sh up
   ```

3. Optional: bootstrap and install local Sentry too:

   ```bash
   cd docker
   ./manage-local.sh up --with-sentry --bootstrap-sentry --install-sentry
   ```

4. Stop the stack:

   ```bash
   ./manage-local.sh down
   ./manage-local.sh down --with-sentry
   ```

### Local endpoints

- Authentik: `http://localhost:9000`
- Temporal gRPC: `localhost:7233`
- Temporal UI: `http://localhost:8080`
- OPA: `http://localhost:8181`
- OpenTelemetry health: `http://localhost:13133/health`
- Local Sentry when enabled: `http://127.0.0.1:9001`

## Sentry note

Local Sentry testing is supported, but it is materially heavier than the rest
of the stack. Expect it to be useful for initial integration validation, but not
something every developer will want running all the time.

Current upstream guidance and deployment notes suggest planning around at least
16 GB RAM for workable self-hosted usage, with 32 GB total memory including
swap being safer for the full stack. The upstream project also positions
self-hosted primarily for low-volume deployments and proofs of concept.

## Notes

- `proxy-tier` is used by services that should be reachable through the nginx
  reverse proxy in production-style deployments.
- `backend-internal` is used by internal service-to-service traffic.
- Sentry is managed via an external `getsentry/self-hosted` checkout under
  `docker/sentry/`, not through the root compose include list.
