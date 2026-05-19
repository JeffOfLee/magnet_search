# qBittorrent Download Engine Setup

`magnet-search` supports qBittorrent as an alternative download engine via its Web API. This guide covers setup for all supported platforms.

## Architecture

The `QbittorrentDownloader` communicates with a running qBittorrent instance through its [Web API](https://github.com/qbittorrent/qBittorrent/wiki/WebUI-API-(qBittorrent-4.1)). The downloader:

1. Logs in to the Web API
2. Adds the torrent (file upload or magnet link)
3. Polls download progress until completion
4. Cleans up the torrent entry (keeping downloaded files)

For CSV downloads, startup recovery also inspects existing qBittorrent tasks. Existing `downloading` tasks are recorded to download metadata after they complete, and existing `stalledDL` tasks are recorded immediately so restarts do not add duplicate torrents.

qBittorrent must be installed and running separately with Web UI enabled.

## Prerequisites

| Platform | Required Package | Notes |
|----------|-----------------|-------|
| Linux | `qbittorrent-nox` | Headless binary with built-in Web UI |
| macOS | `qBittorrent.app` or Docker | The prebuilt macOS app can enable Web UI from Preferences |
| Windows | `qbittorrent` installer | GUI version includes Web UI; run with `--no-splash` |
| Docker | `linuxserver/qbittorrent` | Recommended for headless deployments |

## Linux

### Install qbittorrent-nox

**Debian/Ubuntu:**
```bash
sudo add-apt-repository ppa:qbittorrent-team/qbittorrent-stable
sudo apt update
sudo apt install qbittorrent-nox
```

**Fedora/RHEL:**
```bash
sudo dnf install qbittorrent-nox
```

**Arch Linux:**
```bash
sudo pacman -S qbittorrent-nox
```

### Configure and Run

Create a systemd service or run directly:

```bash
# First run: accepts legal notice, exits for configuration
qbittorrent-nox

# Configure Web UI in ~/.config/qBittorrent/qBittorrent.conf
cat > ~/.config/qBittorrent/qBittorrent.conf << 'EOF'
[Preferences]
WebUI\Enabled=true
WebUI\Port=8080
WebUI\LocalHostAuth=false
WebUI\AuthSubnetWhitelist=127.0.0.1/32
WebUI\AuthSubnetWhitelistEnabled=true
EOF

# Run as daemon (background)
qbittorrent-nox --daemon --webui-port=8080

# Or with explicit profile
qbittorrent-nox --profile=/path/to/config --webui-port=8080
```

For production use, set up a systemd service:

```ini
# /etc/systemd/system/qbittorrent.service
[Unit]
Description=qBittorrent-nox
After=network.target

[Service]
User=qbittorrent
ExecStart=/usr/bin/qbittorrent-nox --webui-port=8080
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

## macOS

The prebuilt macOS `qBittorrent.app` can be used as the `magnet-search` qBittorrent download backend as long as the app is running and Web UI is enabled. This is the simplest local setup when you install qBittorrent from the official `.dmg` or a package manager that installs the GUI app.

### Prebuilt macOS App

1. Install qBittorrent:

   ```bash
   brew install --cask qbittorrent
   ```

   Or download the macOS package from the official qBittorrent website.

2. Open `qBittorrent.app`.

3. Enable the Web UI:
   - Open `qBittorrent > Preferences...`
   - Go to `Web UI`
   - Check `Web User Interface (Remote control)`
   - Set `IP address` to `127.0.0.1` or leave the default if you only use it locally
   - Set `Port` to `8080`
   - Set a username and password

4. Keep `qBittorrent.app` running while using `magnet-search`.

5. Point `magnet-search` at the Web UI:

   ```bash
   magnet-search download movie.torrent --storage downloads/ --engine qbittorrent \
     --qbittorrent-url http://127.0.0.1:8080 \
     --qbittorrent-username admin \
     --qbittorrent-password '<your-web-ui-password>'
   ```

The Web UI endpoint is the control plane used by `magnet-search`; downloaded files are still saved by qBittorrent to the `--storage` path passed when each torrent is added.

### Docker

Use Docker when you want a headless service instead of keeping the GUI app open:

```bash
docker run -d \
  --name=qbittorrent \
  -e PUID=501 \
  -e PGID=20 \
  -e WEBUI_PORT=8080 \
  -p 8080:8080 \
  -p 6881:6881 \
  -p 6881:6881/udp \
  -v ~/qbittorrent-config:/config \
  -v ~/downloads:/downloads \
  --restart unless-stopped \
  linuxserver/qbittorrent:latest
```

Default credentials: `admin` / `adminadmin`. Change the password after first login.

### Build from Source

```bash
# Install dependencies
brew install boost openssl qt@6 cmake pkg-config

# Clone and build
git clone https://github.com/qbittorrent/qBittorrent.git
cd qBittorrent
mkdir build && cd build
cmake .. -DCMAKE_BUILD_TYPE=Release -DGUI=OFF
make -j$(sysctl -n hw.ncpu)
sudo make install
```

Then follow the Linux configuration steps above.

## Windows

1. Download and install [qBittorrent](https://www.qbittorrent.org/download)
2. During installation, check "qBittorrent (Web UI)" component
3. Start qBittorrent with Web UI enabled:

```cmd
"C:\Program Files\qBittorrent\qbittorrent.exe" --no-splash
```

4. Enable Web UI in `Tools > Options > Web UI`:
   - Check "Web User Interface (Remote control)"
   - Set port (default: 8080)
   - Set username and password
   - For local access: uncheck "Bypass authentication for clients on localhost"

Alternatively, edit `%APPDATA%\qBittorrent\qBittorrent.conf`:

```ini
[Preferences]
WebUI\Enabled=true
WebUI\Port=8080
WebUI\LocalHostAuth=false
```

## Docker (All Platforms)

The `linuxserver/qbittorrent` image provides a fully-configured qBittorrent with Web UI:

```bash
docker run -d \
  --name=qbittorrent \
  -e PUID=$(id -u) \
  -e PGID=$(id -g) \
  -e WEBUI_PORT=8080 \
  -e TZ=Asia/Shanghai \
  -p 8080:8080 \
  -p 6881:6881 \
  -p 6881:6881/udp \
  -v ./qbittorrent-config:/config \
  -v ./downloads:/downloads \
  --restart unless-stopped \
  linuxserver/qbittorrent:latest
```

## Configuration Reference

All settings go in `qBittorrent.conf` under the `[Preferences]` section:

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `WebUI\Enabled` | bool | `false` | Enable Web UI |
| `WebUI\Port` | int | `8080` | Web UI listening port |
| `WebUI\UseUPnP` | bool | `true` | Use UPnP for Web UI port |
| `WebUI\LocalHostAuth` | bool | `true` | Require authentication for localhost |
| `WebUI\AuthSubnetWhitelist` | string | `@Invalid()` | IP ranges to bypass auth (e.g. `127.0.0.1/32,192.168.0.0/16`) |
| `WebUI\AuthSubnetWhitelistEnabled` | bool | `true` | Enable subnet auth bypass |
| `WebUI\Username` | string | `admin` | Web UI username |
| `WebUI\Password_PBKDF2` | string | — | PBKDF2 password hash (generated by qBittorrent on first login) |

**Security notes:**

- Password is stored hashed. To reset, delete `WebUI\Password_PBKDF2` from config and restart.
- `LocalHostAuth=false` enables no-auth local access. Only use on trusted single-user machines.
- The subnet whitelist lets specified IP ranges bypass authentication entirely.

## CLI Usage

```bash
# Basic usage with defaults (localhost:8080, admin, no password)
magnet-search download movie.torrent --storage downloads/ --engine qbittorrent

# With custom connection
magnet-search download movie.torrent --storage downloads/ --engine qbittorrent \
  --qbittorrent-url http://192.168.1.100:8080 \
  --qbittorrent-username admin \
  --qbittorrent-password mypassword

# With S3 upload after download
magnet-search download movie.torrent --storage downloads/ --engine qbittorrent \
  --upload s3-upload.toml

# With magnet link
magnet-search download "magnet:?xt=urn:btih:..." --storage downloads/ --engine qbittorrent

# Monitor current qBittorrent downloads, refreshing every 1 second
magnet-search qbittorrent-monitor \
  --qbittorrent-url http://127.0.0.1:8080 \
  --qbittorrent-username admin \
  --qbittorrent-password mypassword
```

Use `--interval` to change the monitor refresh period.

## Troubleshooting

### Connection refused

```
DownloadError: qBittorrent could not find added torrent
```

- Ensure qBittorrent is running and the Web UI port is reachable
- Verify `--qbittorrent-url` matches the running instance
- Check firewall allows the port

### Login failed

```
DownloadError: qBittorrent login failed: Fails.
```

- Verify username and password match Web UI credentials
- On Docker, default password is `adminadmin`
- If password is lost, delete `WebUI\Password_PBKDF2` from config and restart to reset

### Torrent not found after add

```
DownloadError: qBittorrent could not find added torrent
```

This happens when the torrent hash isn't detected within 10 seconds of adding. Possible causes:
- qBittorrent has a slow metadata download phase (especially for magnet links)
- The torrent was immediately removed by qBittorrent (duplicate or invalid)
- Concurrent downloads interfering — try reducing `--download-concurrency` to 1

### Private tracker authentication (403)

Like aria2c, qBittorrent can't bypass tracker-level authentication. If a private tracker returns 403, the torrent's embedded credential has expired. Re-download the `.torrent` file from the tracker website to get a fresh credential.
