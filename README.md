# Minecraft Forge Server (Docker)

Git-managed [itzg/minecraft-server](https://github.com/itzg/docker-minecraft-server) stack for **Forge 1.21.1**. Mods are declared as text lists (Modrinth slugs and CurseForge IDs); the container downloads and updates them on startup.

## Project layout

```
.
├── compose.yaml           # Docker Compose service definition
├── modrinth-mods.txt      # Modrinth slugs (one per line)
├── curseforge-mods.txt    # CurseForge project IDs / slugs (one per line)
├── deploy.sh              # Deploy script (local or CI)
├── .github/workflows/     # GitHub Actions (optional automated deploy)
├── .env.example           # Template for secrets and deploy settings
├── minecraft_data/        # Created at runtime — world, mods, configs (not in Git)
└── README.md
```

## Prerequisites

| Where | Requirement |
|-------|-------------|
| **VPS (IONOS Linux)** | Docker Engine + Docker Compose plugin |
| **Local machine** | Git, Bash, `rsync`, `ssh`, `curl` (Git Bash or WSL on Windows) |
| **CurseForge** | API key from [CurseForge Console](https://console.curseforge.com/) if you use `curseforge-mods.txt` |

## 1. VPS setup (one time)

```bash
# Install Docker (Debian/Ubuntu example)
sudo apt update && sudo apt install -y docker.io docker-compose-plugin
sudo usermod -aG docker "$USER"
# Log out and back in so the docker group applies

sudo mkdir -p /opt/minecraft-server
sudo chown "$USER:$USER" /opt/minecraft-server
```

Clone or copy this repository to `/opt/minecraft-server` (or your chosen path).

## 2. SSH keys (for passwordless deploy)

On your **local** machine:

```bash
ssh-keygen -t ed25519 -C "minecraft-deploy" -f ~/.ssh/minecraft_deploy
```

Copy the public key to the VPS:

```bash
ssh-copy-id -i ~/.ssh/minecraft_deploy.pub root@YOUR_VPS_IP
```

Test:

```bash
ssh -i ~/.ssh/minecraft_deploy root@YOUR_VPS_IP "echo ok"
```

Optional `~/.ssh/config` entry:

```
Host ionos-mc
  HostName YOUR_VPS_IP
  User root
  IdentityFile ~/.ssh/minecraft_deploy
```

Then set `VPS_HOST=ionos-mc` in `.env`.

## 3. Environment files

### On the VPS

```bash
cd /opt/minecraft-server
cp .env.example .env
nano .env   # set CF_API_KEY, RCON_PASSWORD, MAX_MEMORY
```

| Variable | Purpose |
|----------|---------|
| `MAX_MEMORY` | JVM heap (e.g. `4G`) — maps to `MEMORY` / `MAX_MEMORY` in the container |
| `CF_API_KEY` | Required if `curseforge-mods.txt` has any mods |
| `RCON_PASSWORD` | Change from default; used for remote console |

If your API key contains `$`, escape each `$` as `$$` in `compose.yaml` only. In `.env` you do **not** need to escape `$`.

### Locally (for `deploy.sh`)

Copy `.env.example` to `.env` in the repo root and set:

```bash
VPS_HOST=your.vps.ip
VPS_USER=root
VPS_PATH=/opt/minecraft-server
# DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
```

`.env` is **not** rsync’d to the VPS (secrets stay on the server).

## 4. First boot

On the **VPS**:

```bash
cd /opt/minecraft-server
docker compose pull
docker compose up -d
docker compose logs -f
```

First start downloads Forge, resolves mods from your list files, and creates `./minecraft_data/`. This can take several minutes.

## 5. Migrating from a manual install

1. Stop the old server process.
2. Copy **only** what you need into `minecraft_data/` on the VPS:
   - `world/` → `minecraft_data/world/`
   - `server.properties`, `ops.json`, `whitelist.json` if you use them
   - Do **not** copy old `mods/` if you are switching to list-based installs — let the image repopulate `/data/mods` from your text files.
3. Add every mod you still want to `modrinth-mods.txt` and/or `curseforge-mods.txt`.
4. `docker compose up -d`

## 6. Adding or removing a mod

1. Edit `modrinth-mods.txt` and/or `curseforge-mods.txt` (one entry per line).
2. Commit and push to `main` on GitHub.

If GitHub Actions is configured (see below), the server deploys automatically. Otherwise deploy from your machine:

```bash
chmod +x deploy.sh
./deploy.sh
```

`deploy.sh` will:

1. Post to Discord (if `DISCORD_WEBHOOK_URL` is set)
2. `rsync` the repo to the VPS (excluding `minecraft_data/` and `.env`)
3. SSH in and run `docker compose stop` → `pull` → `up -d`

Removed list entries are cleaned from `/data/mods` on the next start (image default behavior).

### Modrinth slug example

```
create
automodpack
```

URL: `https://modrinth.com/mod/create` → slug is `create`.

### CurseForge example

```
238222
jei
https://www.curseforge.com/minecraft/mc-mods/just-enough-items-jei
```

Use the numeric **Project ID** from the mod’s CurseForge “About” section, or the slug from the URL.

## 7. Deploy with GitHub Actions

Push to `main` can deploy the server without running `deploy.sh` locally. The workflow reuses the same script as local deploy.

### One-time setup

1. **Push this repo to GitHub** and ensure the default branch is `main` (or edit `branches` in `.github/workflows/deploy.yml`).

2. **Create a deploy SSH key** (dedicated key for CI — do not reuse your personal key):

   ```bash
   ssh-keygen -t ed25519 -C "github-actions-minecraft" -f ~/.ssh/github_actions_minecraft -N ""
   ```

3. **Install the public key on the VPS:**

   ```bash
   ssh-copy-id -i ~/.ssh/github_actions_minecraft.pub root@YOUR_VPS_IP
   ```

   The VPS user must be able to run `docker compose` (same as local deploy).

4. **Add repository secrets** in GitHub: **Settings → Secrets and variables → Actions → New repository secret**

   | Secret | Value |
   |--------|--------|
   | `VPS_HOST` | VPS IP or hostname |
   | `VPS_USER` | SSH user (e.g. `root`) |
   | `VPS_PATH` | Deploy directory (e.g. `/opt/minecraft-server`) |
   | `VPS_SSH_KEY` | Full private key contents of `github_actions_minecraft` |
   | `DISCORD_WEBHOOK_URL` | *(optional)* Discord webhook for restart messages |

   Keep `CF_API_KEY` and `RCON_PASSWORD` in the VPS `.env` only — they are not needed in GitHub.

5. **First-time VPS `.env`** must already exist on the server (see section 3) before the first Action run.

### When it runs

| Trigger | Behavior |
|---------|----------|
| **Push to `main`** | Runs when `compose.yaml`, mod lists, `deploy.sh`, or the workflow file change |
| **Manual** | Actions tab → **Deploy Minecraft Server** → **Run workflow** |

Only one deploy runs at a time (`concurrency` group).

### Optional: require approval before deploy

In GitHub: **Settings → Environments → New environment** → name it `production`, enable **Required reviewers**, then add to `.github/workflows/deploy.yml` under the job:

```yaml
environment: production
```

### Optional repository variable

| Variable | Purpose |
|----------|---------|
| `DEPLOY_RSYNC_DELETE` | Set to `true` to delete remote files not in the repo during sync |

### Verify a run

After pushing a mod list change, open **Actions** in GitHub and inspect the workflow log. On success you should see rsync output and remote `docker compose ps`.

## 8. Discord restart alerts

1. Server settings → Integrations → Webhooks → New Webhook.
2. Copy the webhook URL into local `.env`:

```bash
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
# optional custom message:
# DISCORD_RESTART_MESSAGE=Server updating mods — brb!
```

## 9. Useful commands (on VPS)

```bash
cd /opt/minecraft-server

docker compose logs -f          # live logs
docker compose stop             # graceful shutdown
docker compose restart          # quick restart (no image pull)
docker compose exec minecraft rcon-cli say Hello   # in-game message (if rcon-cli available)
```

Graceful shutdown timing is controlled by `STOP_SERVER_ANNOUNCE_DELAY` and `STOP_DURATION` in `compose.yaml`.

## 10. Windows notes

Run `deploy.sh` from **Git Bash** or **WSL**, not PowerShell (unless you port the script). Ensure OpenSSH and rsync are available (`rsync` ships with Git for Windows in many setups).

## 11. Troubleshooting

| Issue | Check |
|-------|--------|
| Forge won’t start | `docker compose logs`; try setting `FORGE_VERSION` in `compose.yaml` |
| CurseForge mod missing | `CF_API_KEY` in VPS `.env`; correct project ID / slug for 1.21.1 |
| Modrinth mod skipped | Slug typo; add `?` suffix for optional mods (e.g. `pl3xmap?`) |
| `Permission denied` on deploy | SSH key loaded; `VPS_USER` can run `docker` without sudo |
| GitHub Action fails on SSH | `VPS_SSH_KEY` includes full key with `BEGIN`/`END` lines; public key is in `authorized_keys` on VPS |
| Action skips on README-only push | Expected — workflow only runs for compose/mod/deploy path changes |
| World not loading | `LEVEL` env (default `world`); world folder under `minecraft_data/world` |

## License

Minecraft EULA applies to server operation. This repository is configuration only; the server image is [itzg/minecraft-server](https://github.com/itzg/docker-minecraft-server).
