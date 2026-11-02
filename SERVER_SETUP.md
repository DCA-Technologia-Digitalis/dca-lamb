# Configuração do servidor — DV Saúde / DV Odonto

## Ubuntu 22.04/24.04

Instale Docker Engine e o plugin Compose v2 conforme a documentação oficial da distribuição do Docker. Depois configure o repositório e `.env` seguindo `DEPLOYMENT.md`.

## Windows

Instale Docker Desktop com WSL2. O Compose de produção é voltado a um host Linux; use o perfil `compose.dev.yaml` para desenvolvimento local.

## Servidor local em WSL2

O servidor Flask/Gunicorn já faz parte do projeto. WSL2 fornece o Ubuntu no Windows; ele não é instalado dentro do aplicativo. Para desenvolvimento, mantenha o checkout em `~/src/DV-Odonto-main` no sistema de arquivos Linux do WSL e execute:

```bash
bash scripts/dev-wsl.sh
```

O script cria um ambiente virtual local, instala as dependências e inicia Flask em `127.0.0.1:5001`, com SQLite de desenvolvimento em `data/dv-wsl.sqlite3`. O acesso pelo Windows é `http://localhost:5001`. Para testar em Android/iPhone na mesma rede, configure `DV_DEV_HOST=0.0.0.0` ao iniciar e libere a porta somente na rede local; use exclusivamente dados fictícios nesse modo. O servidor de desenvolvimento não é apropriado para produção.

## Firewall

Libere apenas 80 e 443 para acesso externo. Não exponha 5432, 6379, 9090, 3000, 9100, 9113, 9121 ou 9187. Grafana deve ser acessado via rede privada ou túnel SSH.

```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

## Certificados TLS

Antes de subir o Compose, instale certificados válidos do domínio em `infra/nginx/certs/fullchain.pem` e `infra/nginx/certs/privkey.pem`. A configuração atual não executa Certbot nem emite/renova certificados automaticamente; configure essa renovação no host e recarregue o Nginx após atualizar os arquivos.
