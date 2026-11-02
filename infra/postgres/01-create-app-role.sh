#!/bin/sh
set -eu

: "${POSTGRES_APP_PASSWORD:?Configure POSTGRES_APP_PASSWORD before initializing PostgreSQL}"

psql \
  --set=ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set="app_user=dvapp" \
  --set="app_password=$POSTGRES_APP_PASSWORD" \
  --file=/opt/dv-init/01-app-role.sql
