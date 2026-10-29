#!/usr/bin/env bash
# Makes pg_dump / pg_restore of version 16 available (they must match the PostgreSQL 16 server).
# 1) already 16 -> nothing to do; 2) the official PostgreSQL apt repository (PGDG); 3) wrappers that run the tools from the postgres:16 image.
# Works on any Ubuntu runner; the default Ubuntu repository of a new release may not carry postgresql-client-16.
set -uo pipefail
have16() { command -v pg_dump >/dev/null 2>&1 && pg_dump --version | grep -q ' 16\.' && pg_restore --version | grep -q ' 16\.'; }
have16 && { pg_dump --version; exit 0; }

. /etc/os-release
sudo install -d /usr/share/postgresql-common/pgdg
if sudo curl -sSfL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc https://www.postgresql.org/media/keys/ACCC4CF8.asc \
   && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" | sudo tee /etc/apt/sources.list.d/pgdg.list >/dev/null \
   && sudo apt-get update -qq && sudo apt-get install -y -qq postgresql-client-16 && have16; then
  pg_dump --version; exit 0
fi

echo "PGDG has no postgresql-client-16 for ${VERSION_CODENAME}; using the tools of the postgres:16 image"
sudo rm -f /etc/apt/sources.list.d/pgdg.list
bin="${RUNNER_TEMP:-/tmp}/pg16bin"; mkdir -p "$bin"
for tool in pg_dump pg_restore psql; do
  cat > "$bin/$tool" <<WRAP
#!/usr/bin/env bash
exec docker run --rm -i --network host -e PGPASSWORD -e PGHOST -e PGPORT -e PGUSER -e PGDATABASE -v /tmp:/tmp -v "\$PWD:\$PWD" -w "\$PWD" postgres:16 $tool "\$@"
WRAP
  chmod +x "$bin/$tool"
done
[ -n "${GITHUB_PATH:-}" ] && echo "$bin" >> "$GITHUB_PATH"
PATH="$bin:$PATH" pg_dump --version | grep -q ' 16\.' || { echo "postgres:16 tools are not usable"; exit 1; }
PATH="$bin:$PATH" pg_dump --version
