# Deployment overview

Agentum ships with a Docker-based deployment flow that supports sandboxed
execution (bubblewrap) and a non-Docker local mode.

## Docker deployment

1. Ensure Docker + Docker Compose are available on the host.
2. Update `config/api.yaml` with the desired external ports:
   - `api.external_port` controls the host port for the backend API.
   - `web.external_port` controls the host port for the React UI.
3. Run the deploy script:

   ```bash
   ./deploy.sh build
   ```

The script builds a new image tag first, updates `.env`, then replaces the
running containers. If anything fails, it rolls back to the previous tag.

### Cleanup

```bash
./deploy.sh cleanup
```

Stops containers and removes `agentum:*` images.

## Mounted paths

The Docker Compose definition exposes the following directories as bind mounts
so changes persist on the host:

- `/config`
- `/data`
- `/logs`
- `/src`
- `/sessions`

These are mounted from the corresponding repo directories when running in
Docker. When running locally (no Docker), the same folders are resolved
relative to the repo root.
