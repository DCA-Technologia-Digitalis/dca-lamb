#!/bin/sh
set -eu
set -o pipefail
# backup.sh — Backup diario do PostgreSQL
# Executado pelo crond dentro do container de backup

BACKUP_DIR="${BACKUP_DIR:-/backups}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"
AGE_RECIPIENT="${BACKUP_AGE_RECIPIENT:-}"
if [ -z "$AGE_RECIPIENT" ]; then
  echo "[backup] ERRO: BACKUP_AGE_RECIPIENT deve conter a chave publica age" >&2
  exit 2
fi
if ! command -v age >/dev/null 2>&1; then
  echo "[backup] ERRO: age nao esta instalado no container de backup" >&2
  exit 2
fi
case "$RETENTION_DAYS" in
  ''|*[!0-9]*) echo "[backup] ERRO: BACKUP_RETENTION_DAYS deve ser inteiro" >&2; exit 2 ;;
esac
DB_HOST="${POSTGRES_HOST:-db}"
DB_PORT="${POSTGRES_PORT:-5432}"
DB_NAME="${POSTGRES_DB:-dv_odonto}"
DB_USER="${POSTGRES_USER:-dvuser}"
TIMESTAMP=$(date +"%Y%m%dT%H%M%SZ")
BACKUP_FILE="${BACKUP_DIR}/dv_odonto_${TIMESTAMP}.sql.gz.age"

mkdir -p "$BACKUP_DIR"

echo "[backup] Iniciando backup: $BACKUP_FILE"

if ! pg_dump \
  -h "$DB_HOST" \
  -p "$DB_PORT" \
  -U "$DB_USER" \
  -d "$DB_NAME" \
  --no-password \
  --format=plain \
  --no-owner \
  --no-acl \
  | gzip \
  | age --encrypt --recipient "$AGE_RECIPIENT" > "$BACKUP_FILE"; then
  echo "[backup] ERRO: falha no pg_dump"
  rm -f "$BACKUP_FILE"
  exit 1
fi

if [ ! -s "$BACKUP_FILE" ]; then
  echo "[backup] ERRO: arquivo de backup vazio" >&2
  rm -f "$BACKUP_FILE"
  exit 1
fi
echo "[backup] Backup cifrado criado: $BACKUP_FILE"

# Remove backups antigos
find "$BACKUP_DIR" \( -name "dv_odonto_*.sql.gz.age" -o -name "dv_odonto_*.sql.gz" \) -mtime "+${RETENTION_DAYS}" -delete
echo "[backup] Limpeza de backups com mais de ${RETENTION_DAYS} dias concluida"

# Lista backups existentes
echo "[backup] Backups disponiveis:"
ls -lh "$BACKUP_DIR"/dv_odonto_*.sql.gz.age "$BACKUP_DIR"/dv_odonto_*.sql.gz 2>/dev/null || echo "  Nenhum"
