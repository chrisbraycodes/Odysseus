# Workspace folder

This directory is the default host mount for the in-browser Workspace IDE.

Docker maps it to `/workspace` inside the Odysseus container. Pick this folder
(or a subfolder) in the UI via **+ → Workspace**, or open `http://localhost:7000/workspace`.

To use a different host path (for example your Desktop), set `WORKSPACE_HOST_PATH`
in `.env` before starting Compose. See `.env.example` and the README **AI agent
setup** section.
