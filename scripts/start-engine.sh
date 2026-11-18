#!/bin/bash
set -euo pipefail
until pg_isready -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" >/dev/null; do
  sleep 1
done
# Only the task-owned database is configured here; no demo datasets are loaded.
frepplectl createdatabase --skip-if-exists
frepplectl migrate --noinput
frepplectl shell -c 'exec(open("/p0/scripts/bootstrap.py").read())'
# Optional original queue-worker mode for chained import/solve operations.
# This is frePPLe's own dispatcher, with the same task and planning Owners.
if [[ "${P0_NATIVE_WORKER_CONTINUOUS:-0}" == "1" ]]; then
  frepplectl runworker --database=default --continuous &
fi
printf '\nServerName localhost\n' >> /etc/apache2/apache2.conf
exec apachectl -D FOREGROUND
