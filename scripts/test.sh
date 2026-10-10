#!/usr/bin/env bash
# Run the Python test suite against a throwaway local Postgres 17.
#
# If TEST_DATABASE_URL is already set (e.g. a CI service container), it is
# used as is. Otherwise a private Postgres is started from the zonky
# embedded-postgres binaries (cached in .cache/, verified by checksum),
# used for the run, and deleted afterwards. Nothing here touches Supabase.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -n "${TEST_DATABASE_URL:-}" ]; then
  exec .venv/bin/python -m pytest jobs/tests -q "$@"
fi

PG_VERSION=17.10.0
case "$(uname -s)-$(uname -m)" in
  Darwin-x86_64) PLATFORM=darwin-amd64 ;;
  Darwin-arm64)  PLATFORM=darwin-arm64v8 ;;
  Linux-x86_64)  PLATFORM=linux-amd64 ;;
  Linux-aarch64) PLATFORM=linux-arm64v8 ;;
  *) echo "Unsupported platform $(uname -s)-$(uname -m)" >&2; exit 1 ;;
esac

CACHE=".cache/postgres-$PG_VERSION-$PLATFORM"
if [ ! -x "$CACHE/bin/postgres" ]; then
  ARTIFACT="embedded-postgres-binaries-$PLATFORM"
  URL="https://repo1.maven.org/maven2/io/zonky/test/postgres/$ARTIFACT/$PG_VERSION/$ARTIFACT-$PG_VERSION.jar"
  DL="$(mktemp -d)"
  echo "Downloading Postgres $PG_VERSION for $PLATFORM (one time)..."
  curl -fsSL "$URL" -o "$DL/pg.jar"
  curl -fsSL "$URL.sha1" -o "$DL/pg.jar.sha1"
  if [ "$(shasum -a 1 "$DL/pg.jar" | cut -d' ' -f1)" != "$(cut -c1-40 "$DL/pg.jar.sha1")" ]; then
    echo "Checksum mismatch for $URL" >&2; exit 1
  fi
  mkdir -p "$DL/jar" "$CACHE"
  unzip -q "$DL/pg.jar" -d "$DL/jar"
  tar -xJf "$DL"/jar/*.txz -C "$CACHE"
  rm -rf "$DL"
fi

PORT="${TEST_DB_PORT:-54329}"
DATA="$(mktemp -d)"
cleanup() {
  "$CACHE/bin/pg_ctl" -D "$DATA" -m immediate stop >/dev/null 2>&1 || true
  rm -rf "$DATA"
}
trap cleanup EXIT

"$CACHE/bin/initdb" -D "$DATA" -U postgres --auth=trust -E UTF8 --locale=C >/dev/null
"$CACHE/bin/pg_ctl" -D "$DATA" -l "$DATA/server.log" -w \
  -o "-p $PORT -k $DATA -c listen_addresses=127.0.0.1 -c fsync=off" start >/dev/null

TEST_DATABASE_URL="postgresql://postgres@127.0.0.1:$PORT/postgres" \
  .venv/bin/python -m pytest jobs/tests -q "$@"
