# Roteiro de homologacao isolada

Este roteiro usa somente a imagem de teste `compose.test.yaml`, um volume Compose novo e um SQLite sintetico criado pelo projeto. Nao use o `compose.yaml`, `.env`, bancos locais existentes, backups ou credenciais de producao. O PostgreSQL de teste publica a porta apenas em `127.0.0.1`.

## Preparar os servicos

Em PowerShell, a partir da raiz do projeto:

```powershell
$env:DCA_TEST_POSTGRES_PASSWORD = 'DCAHomologacao-uma-senha-temporaria-aleatoria'
docker compose --env-file .env.homologation.test.example -p dca-homologation-test -f compose.test.yaml config -q
docker compose --env-file .env.homologation.test.example -p dca-homologation-test -f compose.test.yaml up -d --wait
```

Se o daemon Docker nao iniciar, a validacao PostgreSQL/Compose fica pendente. Nao rode `down -v`: o volume guarda os registros sinteticos para a checagem de persistencia e deve ser preservado.

## Criar e migrar dados sinteticos

Use uma pasta nova de teste; o gerador se recusa a sobrescrever um destino existente:

```powershell
New-Item -ItemType Directory -Force .pytest-run-postgres | Out-Null
.\.venv\Scripts\python.exe tools\create_homologation_db.py .pytest-run-postgres\source.sqlite3
$env:DATABASE_URL = "postgresql://dca_test:$($env:DCA_TEST_POSTGRES_PASSWORD)@127.0.0.1:55439/dca_homologation"
.\.venv\Scripts\python.exe tools\migrate_sqlite_to_postgres.py .pytest-run-postgres\source.sqlite3
.\.venv\Scripts\python.exe tools\migrate_sqlite_to_postgres.py .pytest-run-postgres\source.sqlite3 --apply
$env:DV_TEST_POSTGRES_URL = $env:DATABASE_URL
.\.venv\Scripts\python.exe -m pytest tests\test_postgres_homologation.py -q
```

O primeiro comando de migracao e somente leitura da origem e valida que o destino esta vazio. `--apply` importa apenas para o PostgreSQL temporario definido por `DATABASE_URL`; nunca aponte esse comando para producao. O importador agora inclui plano, modulos, assinaturas, segredo MFA cifrado e hashes de recuperacao.

## Verificar aplicacao e persistencia

Com o mesmo `DATABASE_URL` ainda definido, em outro terminal:

```powershell
$env:SECRET_KEY = 'chave-temporaria-de-homologacao-com-mais-de-32-caracteres'
$env:SESSION_COOKIE_SECURE = 'false'
.\.venv\Scripts\python.exe app.py
```

Consulte `http://127.0.0.1:5001/healthz`: deve responder HTTP 200 e `database: ok`. Depois reinicie apenas o servico da homologacao:

```powershell
docker compose --env-file .env.homologation.test.example -p dca-homologation-test -f compose.test.yaml restart postgres-test
```

Repita `/healthz` e uma consulta de contagem ao registro sintetico no PostgreSQL. O dado deve persistir. Para revisar integridade, indices e constraints:

```powershell
docker compose --env-file .env.homologation.test.example -p dca-homologation-test -f compose.test.yaml exec postgres-test psql -U dca_test -d dca_homologation -c "SELECT count(*) FROM pacientes;"
docker compose --env-file .env.homologation.test.example -p dca-homologation-test -f compose.test.yaml exec postgres-test psql -U dca_test -d dca_homologation -c "SELECT indexname FROM pg_indexes WHERE schemaname=current_schema() ORDER BY indexname;"
docker compose --env-file .env.homologation.test.example -p dca-homologation-test -f compose.test.yaml exec postgres-test psql -U dca_test -d dca_homologation -c "SELECT conname, convalidated FROM pg_constraint WHERE connamespace=current_schema()::regnamespace ORDER BY conname;"
```

O volume se chama `dca-homologation-test_postgres_homologation_data`; mantenha-o enquanto precisar dos dados de ensaio. Nao apague volumes sem confirmar manualmente que esse nome identifica somente a homologacao descartavel.

## Testes locais e vulnerabilidades

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp=.pytest-run-local
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pip_audit --local
```

Em ambientes que nao permitem usar `%TEMP%`, informe uma pasta de teste gravavel com `--basetemp`. A analise `pip-audit` consulta o indice publico de avisos de vulnerabilidade; nao envia codigo nem dados do sistema.
