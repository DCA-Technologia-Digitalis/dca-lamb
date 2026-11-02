# Arquitetura — DV Saúde / DV Odonto

## Componentes

```text
Internet → Nginx (HTTPS/HTTP2, limites e arquivos estáticos) → Gunicorn/Flask
                                                        ├─ SQLite (desenvolvimento/testes)
                                                        └─ PostgreSQL (produção configurada)
Redis atende sessões de servidor e limites de requisição compartilhados.
Prometheus coleta métricas; Grafana consulta Prometheus.
O serviço de backup executa pg_dump diariamente.
```

- Nginx publica apenas as portas 80 e 443. Os certificados são montados de `infra/nginx/certs/`.
- Flask/Gunicorn serve a aplicação. Uploads clínicos ficam no volume privado `uploads_data` e passam por uma rota autenticada; `/static/uploads/` é negada.
- PostgreSQL e Redis ficam na rede interna do Compose.
- Prometheus e exporters coletam métricas. Grafana não publica porta no host.

## Banco

SQLite é usado no desenvolvimento local e nos testes. A produção Docker usa `DATABASE_URL` PostgreSQL e um pool por worker Gunicorn. A criação do esquema não migra registros do SQLite; migração de dados deve ser realizada e verificada antes da troca.

## Segurança e operação

- TLS 1.2/1.3, HTTP/2, HSTS, cabeçalhos de segurança, CSRF e bcrypt.
- Nginx registra URI sem query string para evitar guardar parâmetros que possam conter dados de saúde.
- O escopo de pacientes e da farmácia é vinculado ao CNES da conta.
- Nenhum endpoint de monitoramento público deve expor informação clínica.
- Apache, HTTP/3 e Kubernetes não fazem parte da configuração validada deste projeto.
