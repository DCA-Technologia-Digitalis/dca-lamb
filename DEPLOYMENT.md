# Implantação — DV Saúde / DV Odonto

## Requisitos

- Docker Engine 24+ e Docker Compose v2 em host Linux.
- DNS apontando para o servidor e certificados válidos em `infra/nginx/certs/fullchain.pem` e `privkey.pem`.
- `.env` configurado com `DOMAIN`, senhas fortes, `DATABASE_URL`, `POSTGRES_APP_PASSWORD` e uma chave pública `BACKUP_AGE_RECIPIENT`.
- A senha de `dvapp` deve ser uma chave hexadecimal segura e igual à senha usada na URL PostgreSQL. A senha do `dvuser` é administrativa e diferente.

## Volume PostgreSQL novo

```bash
test -f .env || cp .env.example .env
# Edite .env sem substituir um arquivo existente.
docker compose config --quiet
docker compose up -d --build
docker compose ps
docker compose logs -f web
```

No primeiro volume, o entrypoint PostgreSQL cria `dvapp` como conta da aplicação sem superusuário. A aplicação conecta como `dvapp`; `dvuser` fica reservado à administração e backups.

## Volume PostgreSQL existente

Faça e confira um backup antes de alterar permissões. Atualize `.env` com `POSTGRES_APP_PASSWORD` e o `DATABASE_URL` apontando para `dvapp`. Depois que o container atualizado estiver disponível, execute uma única vez:

```bash
docker compose exec db /bin/sh /docker-entrypoint-initdb.d/01-create-app-role.sh
docker compose up -d --force-recreate web postgres-exporter
```

O script cria o papel não privilegiado e transfere a propriedade de tabelas/sequências existentes. Ele não apaga registros. Confira o backup e a conexão da aplicação antes de retomar o tráfego.

## Migração SQLite para PostgreSQL

A criação do esquema não copia dados. Faça backup íntegro do SQLite e prepare PostgreSQL vazio. Rode primeiro a conferência (somente lê a origem e prepara o esquema vazio no destino):

```bash
docker compose run --rm -v /CAMINHO/ABSOLUTO/origem.db:/migration/origem.db:ro web \
  python tools/migrate_sqlite_to_postgres.py /migration/origem.db
```

Revise as contagens sem imprimir registros clínicos. Só então importe com `--apply` para o schema vazio:

```bash
docker compose run --rm -v /CAMINHO/ABSOLUTO/origem.db:/migration/origem.db:ro web \
  python tools/migrate_sqlite_to_postgres.py /migration/origem.db --apply
```

A ferramenta recusa origem inconsistente ou destino com registros e nunca trunca dados. Valide contagens, login, agenda, anexos e relatórios antes de apontar usuários ao novo banco. Preserve o arquivo SQLite original como retorno.

## Desenvolvimento local

- WSL2: `bash scripts/dev-wsl.sh` (servidor em `http://localhost:5001`).
- Compose: `docker compose -f compose.dev.yaml up -d`.

## Verificações e atualização

- Aplicação: `https://DOMINIO/healthz`
- Nginx: `docker compose exec nginx nginx -t`
- PostgreSQL: `docker compose exec db pg_isready`
- Redis: `docker compose exec redis redis-cli -a SENHA ping`

Registre a versão atual e confira backup antes de atualizar:

```bash
docker compose pull
docker compose build --pull
docker compose up -d
```

Grafana não publica porta no host; acesse por rede privada ou túnel SSH. Para rollback e restauração, consulte `BACKUP_AND_RECOVERY.md`.
