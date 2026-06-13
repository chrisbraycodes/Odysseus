# Workspace folder

This directory is the default host mount for the in-browser Workspace IDE.

Docker maps it to `/workspace` inside the Odysseus container. Files are **kept in sync**
automatically — there is no separate copy step when you edit, import, or download
inside the mount.

## Pick a project folder

In chat: **+ → Workspace**. You can:

- Browse under `/workspace` in the container
- Paste the matching folder path from your computer (when `WORKSPACE_HOST_PATH` is set)
- **Import folder/files** to copy content into the current location
- **Download** files or folders (zip) from the file tree panel

## Use your own host folder

Set `WORKSPACE_HOST_PATH` in `.env` before starting Compose, then recreate the container:

```bash
# Windows example
WORKSPACE_HOST_PATH=C:/Users/you/Desktop

# Linux/macOS example
WORKSPACE_HOST_PATH=/home/you/projects
```

Restart: `docker compose up -d --force-recreate odysseus`

## Dev servers (Docker)

Default: `WORKSPACE_DEV_EXEC=host` — run `npm start` / `vite` **on your computer**
in the host workspace folder. Odysseus edits files via the bind mount; preview opens
at `http://127.0.0.1:3000` (or `:5173` for Vite) on the host.

Set `WORKSPACE_DEV_EXEC=container` to run dev servers inside Docker instead (uses
the mapped preview ports in `docker-compose.yml`).

See `.env.example` and the README **AI agent setup** section.
