# Monitoramento — DV Saúde / DV Odonto

Prometheus coleta a aplicação, Nginx, PostgreSQL, Redis e node-exporter. Regras em `infra/prometheus/alerts.yml` detectam alvo indisponível, taxa elevada de respostas 5xx e uso de disco.

Grafana é provisionado com Prometheus e o dashboard do projeto. A porta 3000 não é publicada no host; use acesso privado ou túnel SSH. Configure a senha inicial em `GRAFANA_ADMIN_PASSWORD` no `.env`.

O projeto não configura destino de notificação para alertas. Para receber notificações, conecte Alertmanager a um canal operado pela equipe e valide o fluxo de ponta a ponta.

Verificações rápidas:

```bash
docker compose ps
docker compose logs --since=15m web nginx prometheus grafana
docker compose exec nginx nginx -t
docker compose exec db pg_isready
```
