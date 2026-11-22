# Production deployment

The public instance is <https://omicsbase.utu.fi>.

## Hosts

- **Web host**: a CSC cPouta VM runs LibreChat, the OpenHands image and the VM engine with Docker Compose (`librechat/` with [docker-compose.override.yml](../deployment/docker-compose.override.yml) and [librechat.yaml](../deployment/librechat.yaml)).
- **TLS and routing**: Caddy serves `omicsbase.utu.fi`, `openhands.omicsbase.utu.fi` and `admin.omicsbase.utu.fi` with HARICA certificates; see [Caddyfile.production](../deployment/Caddyfile.production). Reports (`openhands.…`) are only reachable through the OmicsBase login.
- **Notes engine**: note cells run on LUMI-K, one pod per note; see [deployment/lumi-k](../deployment/lumi-k). Reports sites are rendered by the engine on the VM.

## Login

University of Turku single sign-on (SAML) and Google.

## Images

The images in use are recorded in [local-image-inventory.json](../deployment/local-image-inventory.json). Builds: [engine](ENGINE_IMAGE.md), `scripts/build_librechat.py`, `scripts/build_openhands.py`. The forks and how their pins are updated are described in [UPSTREAM.md](../UPSTREAM.md).

## Search engines

Caddy serves `robots.txt` from [deployment/seo](../deployment/seo) and `sitemap.xml` from `/srv/omicsbase/seo`. The sitemap lists the front page and every publicly shared note; [scripts/build_sitemap.sh](../scripts/build_sitemap.sh) rebuilds it nightly from cron (03:15 UTC).

## Maintenance

Findings and post-update checks: [MAINTENANCE.md](MAINTENANCE.md), [VERIFICATION.md](VERIFICATION.md).
