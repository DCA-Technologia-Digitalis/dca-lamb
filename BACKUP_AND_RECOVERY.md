# Backup e recuperação — DV Saúde / DV Odonto

## Backup automático

O serviço `backup` executa `pg_dump` diariamente às 02:00 UTC e cifra o fluxo comprimido com age. O volume `backups_data` guarda arquivos `.sql.gz.age`, com retenção padrão de 30 dias (`BACKUP_RETENTION_DAYS`). Configure `BACKUP_AGE_RECIPIENT` com a chave pública age antes de iniciar.

Gere e proteja uma identidade age fora do projeto/servidor. A chave privada não deve estar no `.env`, container ou volume de backup. Replique os arquivos cifrados para armazenamento externo independente; o volume local sozinho não cobre perda do host. A cifragem dos volumes PostgreSQL/uploads depende de criptografia de disco no host.

## Backup manual e conferência

```bash
docker compose exec backup /bin/sh /backup.sh
docker compose exec backup find /backups -maxdepth 1 -type f -name '*.age' -ls
```

A execução verifica status dos processos da pipeline e que o arquivo cifrado não está vazio. Faça ensaio de descriptografia/restauração em ambiente isolado; não há restauração automática nem canal de notificação configurado.

## Restauração

Com age instalado no host e uma identidade privada protegida fora do repositório, use a chave somente durante o procedimento:

```bash
age --decrypt --identity /caminho-seguro/age-identity.txt /caminho/backup.sql.gz.age \
  | gunzip -c \
  | docker compose exec -T db psql -U dvuser -d dv_odonto
```

Restaure em instalação isolada ou janela de manutenção: `psql` aplicará o dump ao banco informado. Valide contagens e funcionalidades antes de reabrir o serviço.

## Ensaio de restauração

Crie um banco temporário com credenciais administrativas, restaure o dump e valide estrutura/contagens. Apague apenas o banco temporário após revisão manual; nunca use o banco de produção como alvo de ensaio.

## Migração SQLite para PostgreSQL

Use `tools/migrate_sqlite_to_postgres.py` conforme `DEPLOYMENT.md`. A ferramenta faz leitura SQLite e exige destino sem registros; não substitui backup nem validação funcional. A inicialização do esquema PostgreSQL não copia registros automaticamente.
