# sciona-infra

Infrastructure and web product repository for Sciona.

This repo owns:
- platform API and workflows
- frontend website
- Docker and deployment assets
- Supabase schema and migrations
- infra-facing tests and operational docs

It depends on the sibling core repo at `../ageo-matcher` for shared deterministic
logic. For local development, install both repos in editable mode.

## Local setup

```bash
cd ../ageo-matcher
pip install -e '.[all]'

cd ../sciona-infra
pip install -e .
```

## Run API

```bash
uvicorn sciona_infra.api.app:app --reload
```
