# The engine on your own server

An OmicsBase install runs notes' R on the machine it is installed on. To run it on a bigger
machine instead, start the engine there and point the install at it. Nothing on the server
needs to reach the install, so the install can stay on a laptop behind a home router.

On the server (Docker with Compose, a DNS name pointing at it, ports 80 and 443 open):

```bash
mkdir omicsbase-engine && cd omicsbase-engine
for f in docker-compose.yml Caddyfile .env.example; do
  curl -fsSLO https://raw.githubusercontent.com/0xMuluh/omicsbase/main/local/engine-server/$f
done
cp .env.example .env
```

In `.env`, set `ENGINE_DOMAIN` to the server's name and `OMICSBASE_ENGINE_SECRET` to the value
in the install's `.env`. Then `docker compose up -d`; Caddy gets the HTTPS certificate.

On the install, add `ENGINE_URL=https://<the server's name>` to `.env` and run
`docker compose up -d`. New notes run on the server; files of notes made before stay on the
machine that ran them.
