"""Configuracao do Gunicorn para o DV Saude.

Os valores podem ser sobrescritos por variaveis de ambiente. Os workers sao
calculados a partir da CPU disponivel, limitados para evitar consumo excessivo
de memoria em ambientes pequenos.
"""

import os

bind = "0.0.0.0:" + os.environ.get("PORT", "8000")
workers = int(
    os.environ.get(
        "GUNICORN_WORKERS",
        "2",
    )
)
threads = int(os.environ.get("GUNICORN_THREADS", "2"))
worker_class = os.environ.get("GUNICORN_WORKER_CLASS", "sync")
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "60"))
graceful_timeout = int(os.environ.get("GUNICORN_GRACEFUL_TIMEOUT", "30"))
keepalive = int(os.environ.get("GUNICORN_KEEPALIVE", "5"))
max_requests = int(os.environ.get("GUNICORN_MAX_REQUESTS", "1000"))
max_requests_jitter = int(os.environ.get("GUNICORN_MAX_REQUESTS_JITTER", "50"))
worker_tmp_dir = os.environ.get("GUNICORN_WORKER_TMP_DIR", "/dev/shm")
accesslog = "-"
access_log_format = '%(h)s %(t)s "%(m)s %(U)s %(H)s" %(s)s %(B)s %(L)s'
errorlog = "-"
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")
proc_name = "dv-saude"
forwarded_allow_ips = os.environ.get("GUNICORN_FORWARDED_ALLOW_IPS", "127.0.0.1")


def worker_exit(server, worker):
    # Each worker owns an independent PostgreSQL connection pool.
    from database import close_postgres_pool

    close_postgres_pool()
