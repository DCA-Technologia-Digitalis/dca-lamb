FROM postgres:16.8-alpine

RUN apk add --no-cache age
COPY backup.sh /backup.sh
RUN chmod 0555 /backup.sh

CMD ["crond", "-f", "-d", "8"]
