# DV Saúde / DV Odonto

## Portal comercial DCA Software

A estrutura SaaS, o Plano Geral pendente, os paineis e os limites desta etapa estao documentados em [DCA_SAAS.md](DCA_SAAS.md). Nao ha gateway conectado, cobranca, precos definidos ou ativacao por pagamento simulado.

Plataforma web em Flask para atendimento odontológico, prontuário SOAP, agenda, documentos e farmácia SUS. A interface funciona como PWA em navegadores Android e iOS; não é um aplicativo nativo.

## Estado e limites conhecidos

- SQLite para desenvolvimento/testes; PostgreSQL e Redis são configurados para implantação via Docker Compose.
- O esquema inclui pacientes, agenda, SOAP, odontograma, anexos privados, organizações associadas inicialmente a unidades CNES e eventos mínimos de auditoria.
- A cópia SQLite → PostgreSQL é explícita e exige destino vazio. Não ocorre automaticamente ao iniciar o aplicativo.
- FHIR/RNDS/e-SUS ainda não têm conectores configurados. É necessário definir versão, perfil, ambiente, autenticação e credenciais.
- O monitoramento não tem destino de notificação configurado.
- Não existe credencial padrão para demonstração. Use dados fictícios em desenvolvimento.

## Desenvolvimento

No WSL2, mantenha o checkout dentro do sistema de arquivos Linux e execute:

```bash
bash scripts/dev-wsl.sh
```

O servidor fica em `http://localhost:5001`, usando banco SQLite local separado em `data/dv-wsl.sqlite3`.

No Windows, crie um ambiente virtual, instale `requirements.txt`, defina `DV_DATABASE_PATH` para um arquivo de desenvolvimento e `SECRET_KEY` aleatória, e inicie `python app.py`. O servidor local usa loopback por padrão.

## Produção

Consulte [DEPLOYMENT.md](DEPLOYMENT.md), [SERVER_SETUP.md](SERVER_SETUP.md) e [SECURITY.md](SECURITY.md). PostgreSQL e Redis não devem ser publicados à internet. Configure certificados TLS e senhas únicas antes de iniciar o Compose.

Para uma instalação PostgreSQL existente, faça backup antes de provisionar o papel não privilegiado `dvapp`. O script de criação automática de papéis só roda em volumes PostgreSQL novos; o procedimento para volumes existentes está em `DEPLOYMENT.md`.

## Backup e migração

Consulte [BACKUP_AND_RECOVERY.md](BACKUP_AND_RECOVERY.md). Backups automáticos são cifrados com age; mantenha a chave privada fora do servidor e teste a restauração em ambiente isolado.

Para conferir uma migração SQLite sem importar registros, use `tools/migrate_sqlite_to_postgres.py CAMINHO_DO_SQLITE`. A importação exige backup verificado, revisão das contagens e `--apply`; o PostgreSQL de destino precisa estar vazio.

## Testes

Use um ambiente virtual isolado e instale o conjunto de testes (que inclui as dependências de execução usadas pelo Docker):

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-test.txt
.\.venv\Scripts\python.exe -m pytest -q
```

No Linux/WSL, os equivalentes são `python3 -m venv .venv`, `.venv/bin/python -m pip install -r requirements-test.txt` e `.venv/bin/python -m pytest -q`.

O Docker instala apenas `requirements.txt`; `requirements-test.txt` inclui esse mesmo conjunto de runtime e acrescenta pytest para desenvolvimento/CI.

Os testes usam bancos temporários e dados fictícios. Testes que dependem de serviços externos devem ser executados em homologação isolada, nunca contra a base de produção.

```bash
python -m pytest -q
```
