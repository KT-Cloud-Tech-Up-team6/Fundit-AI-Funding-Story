#!/usr/bin/env bash
# Verify the packaged SQL on a disposable database; no deployment credentials used.
set -euo pipefail
image="${1:?Usage: bash scripts/verify_migration_image.sh IMAGE}"
database="funding-story-migration-check-${RANDOM}-$$"
cleanup() { docker rm -fv "$database" >/dev/null 2>&1 || true; }
trap cleanup EXIT

docker run -d --name "$database" \
  -e POSTGRES_DB=funding_story_ai \
  -e POSTGRES_USER=funding_ai \
  -e POSTGRES_PASSWORD=migration-test-only \
  postgres:16 >/dev/null
for attempt in {1..60}; do
  if docker exec "$database" pg_isready -U funding_ai -d funding_story_ai >/dev/null 2>&1; then
    break
  fi
  if [ "$attempt" -eq 60 ]; then
    docker logs "$database"
    exit 1
  fi
  sleep 1
done

migrate() {
  docker run --rm --platform linux/amd64 --network "container:$database" \
    -e FLYWAY_URL=jdbc:postgresql://localhost:5432/funding_story_ai \
    -e FLYWAY_USER=funding_ai \
    -e FLYWAY_PASSWORD=migration-test-only \
    "$image" "$@"
}
migrate migrate
migrate migrate
migrate validate

versions="$(docker exec "$database" psql -U funding_ai -d funding_story_ai -Atc \
  "SELECT string_agg(version, ',' ORDER BY installed_rank) FROM public.flyway_schema_history WHERE success AND version IS NOT NULL")"
[ "$versions" = '1,2' ]
tables="$(docker exec "$database" psql -U funding_ai -d funding_story_ai -Atc \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name IN ('ai_records','ai_requests','checkpoint_migrations','checkpoints','checkpoint_blobs','checkpoint_writes')")"
[ "$tables" = '6' ]
printf 'Migration image verified: V1/V2 applied once, six application tables present.\n'
