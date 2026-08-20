# deploy/etc

The files actually running on the GCP box, kept in the repo so the deployment is
reproducible rather than reconstructed from memory.

| File | Installed at |
|---|---|
| `Caddyfile` | `/etc/caddy/Caddyfile` |
| `shruti-core.service` | `/etc/systemd/system/shruti-core.service` |
| `shruti-gateway.service` | `/etc/systemd/system/shruti-gateway.service` |

Two things in here were learned the hard way and are commented in place:

- Caddy logs to **journald**, not to a file. The packaged `caddy.service` ships
  a sandboxed `ReadWritePaths` that does not include `/var/log/caddy`, so a
  `log { output file ... }` block makes Caddy exit 1 at startup before it ever
  requests a certificate.
- The web root is **`/var/www/shruti`**, not the home directory. A home
  directory is `0750`, the `caddy` user cannot traverse it, and every request
  returns 403 with nothing useful in the log.

No Docker. A venv plus systemd plus Caddy is fewer moving parts than building a
2 GB image and pushing it to a registry, and the artifacts have to be copied
either way.
