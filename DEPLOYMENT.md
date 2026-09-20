# CRUNCHY BAG — PRODUCTION SERVER DEPLOYMENT GUIDE

A practical, production-grade deployment plan for hosting the Crunchy Bag backend (Django 5.1, Daphne ASGI, PostgreSQL, Redis, Celery, Channels WebSockets, and Nginx with Cloudflare).

---

## 1. Server Hardware Sizing (For 5,000–10,000 Concurrent Users)

| Metric | Minimum (Single VPS) | Recommended Production |
|---|---|---|
| **OS** | Ubuntu 22.04 / 24.04 LTS | Ubuntu 24.04 LTS |
| **CPU** | 2 vCPUs | 4–8 vCPUs |
| **RAM** | 4 GB | 8–16 GB |
| **Storage** | 40 GB NVMe SSD | 80–160 GB NVMe SSD |
| **Network** | 1 Gbps | 1 Gbps + Cloudflare CDN |

---

## 2. Infrastructure Architecture on the Server

```
[ Internet / Clients ]
         │ (HTTPS / WSS)
         ▼
  [ Cloudflare CDN & WAF ] (SSL, DDoS, Edge Cache, WebSockets Enabled)
         │
         ▼
    [ Nginx ] (Reverse Proxy :80, :443)
         │
         ├──▶ HTTP Traffic (`/api/`, `/admin/`) ──▶ Daphne ASGI (:8000)
         ├──▶ WebSocket (`/ws/`)               ──▶ Daphne ASGI (:8000)
         └──▶ Static Assets (`/static/`)        ──▶ Local Disk (`/var/www/static`)
                                                       │
                           ┌───────────────────────────┴───────────────────────────┐
                           ▼                                                       ▼
                [ PostgreSQL 16 ]                                           [ Redis 8 ]
                (Durable ACID DB)                                          (Cache / Channels / Celery)
                                                                                   │
                                                                                   ▼
                                                                        [ Celery Workers & Beat ]
                                                                        (Background Async Tasks)
```

---

## 3. Step-by-Step Server Setup (Ubuntu Linux)

### Step 1: Base System Updates & Dependencies
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-venv python3-pip python3-dev \
    libpq-dev postgresql postgresql-contrib redis-server nginx \
    git curl certbot python3-certbot-nginx
```

---

### Step 2: Configure PostgreSQL Database
```bash
sudo -u postgres psql
```
Inside the `psql` console:
```sql
CREATE DATABASE crunchy_db;
CREATE USER crunchy_user WITH PASSWORD 'YourStrongPasswordHere!@#$';
ALTER ROLE crunchy_user SET client_encoding TO 'utf8';
ALTER ROLE crunchy_user SET default_transaction_isolation TO 'read committed';
ALTER ROLE crunchy_user SET timezone TO 'UTC';
GRANT ALL PRIVILEGES ON DATABASE crunchy_db TO crunchy_user;
\q
```

---

### Step 3: Configure Redis
Verify Redis is running:
```bash
sudo systemctl enable redis-server
sudo systemctl restart redis-server
redis-cli ping
# Output should be: PONG
```

---

### Step 4: Clone & Setup the Django Backend
```bash
# Recommended deployment directory
sudo mkdir -p /var/www/crunchy_backend
sudo chown -R $USER:$USER /var/www/crunchy_backend
cd /var/www/crunchy_backend

# Clone your repo or copy files
git clone <YOUR_GIT_REPO_URL> .

# Setup Python Virtual Environment
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

---

### Step 5: Configure Production `.env`
Create `/var/www/crunchy_backend/.env`:
```ini
SECRET_KEY=production-ultra-secure-random-secret-key-replace-me-123456
DEBUG=False
ALLOWED_HOSTS=api.yourdomain.com,127.0.0.1,localhost

# PostgreSQL connection
DATABASE_URL=postgresql://crunchy_user:YourStrongPasswordHere!@#$@127.0.0.1:5432/crunchy_db

# Redis connection
REDIS_URL=redis://127.0.0.1:6379/1
CELERY_BROKER_URL=redis://127.0.0.1:6379/0
CELERY_RESULT_BACKEND=redis://127.0.0.1:6379/0
CHANNEL_LAYER_TYPE=redis

# CORS Settings for your React / Next.js Frontends
CORS_ALLOWED_ORIGINS=https://order.yourdomain.com,https://pos.yourdomain.com,https://kds.yourdomain.com
```

---

### Step 6: Database Migrations & Static Files
```bash
source .venv/bin/activate
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py setup_initial_superuser
```

---

## 4. Systemd Process Management (Auto-restart on crash & reboot)

### Service 1: Daphne ASGI Server (`/etc/systemd/system/daphne.service`)
```ini
[Unit]
Description=Daphne ASGI Server for Crunchy Bag
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/var/www/crunchy_backend
ExecStart=/var/www/crunchy_backend/.venv/bin/daphne \
    -b 127.0.0.1 \
    -p 8000 \
    crunchy_backend.asgi:application
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

### Service 2: Celery Worker (`/etc/systemd/system/celery.service`)
```ini
[Unit]
Description=Celery Worker for Crunchy Bag
After=network.target redis-server.service

[Service]
User=www-data
Group=www-data
WorkingDirectory=/var/www/crunchy_backend
ExecStart=/var/www/crunchy_backend/.venv/bin/celery \
    -A crunchy_backend worker \
    -l info \
    --concurrency=4
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### Service 3: Celery Beat Scheduler (`/etc/systemd/system/celerybeat.service`)
```ini
[Unit]
Description=Celery Beat Scheduler for Crunchy Bag
After=network.target redis-server.service

[Service]
User=www-data
Group=www-data
WorkingDirectory=/var/www/crunchy_backend
ExecStart=/var/www/crunchy_backend/.venv/bin/celery \
    -A crunchy_backend beat \
    -l info
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### Enable and Start All Services:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now daphne celery celerybeat
sudo systemctl status daphne
```

---

## 5. Nginx Reverse Proxy with WebSocket Upgrade

Create `/etc/nginx/sites-available/crunchy_backend`:
```nginx
upstream daphne_backend {
    server 127.0.0.1:8000;
}

server {
    server_name api.yourdomain.com;

    client_max_body_size 25M;

    # Static Files served directly by Nginx (fast!)
    location /static/ {
        alias /var/www/crunchy_backend/staticfiles/;
        expires 30d;
        add_header Cache-Control "public, no-transform";
    }

    # Media Uploads (Logos, Images)
    location /media/ {
        alias /var/www/crunchy_backend/media/;
        expires 7d;
    }

    # WebSockets (/ws/)
    location /ws/ {
        proxy_pass http://daphne_backend;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_redirect off;

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket timeouts for persistent connections
        proxy_read_timeout 86400s;
        proxy_send_timeout 86400s;
    }

    # REST APIs & Django Admin
    location / {
        proxy_pass http://daphne_backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Enable the Nginx configuration:
```bash
sudo ln -s /etc/nginx/sites-available/crunchy_backend /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

---

## 6. SSL & Cloudflare Configuration

1. **Free Let's Encrypt SSL** (via Certbot):
   ```bash
   sudo certbot --nginx -d api.yourdomain.com
   ```
2. **Cloudflare Settings**:
   - Set DNS `A` record pointing `api.yourdomain.com` to your server IP.
   - Set SSL/TLS encryption mode to **Full (Strict)**.
   - Go to **Network** settings in Cloudflare and verify **WebSockets is ON**.
   - Enable **Auto Minify** and **Brotli** compression.

---

## 7. Daily Backup & Maintenance Commands

### Automated Database Backup Cron Job
Add to `sudo crontab -e`:
```bash
# Nightly backup at 02:00 AM
0 2 * * * pg_dump -U crunchy_user -h 127.0.0.1 crunchy_db | gzip > /var/backups/crunchy_db_$(date +\%Y\%m\%d).sql.gz
```

### Quick Deploy / Update Script (`deploy.sh`)
```bash
#!/bin/bash
cd /var/www/crunchy_backend
git pull origin main
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
sudo systemctl restart daphne celery celerybeat
echo "Crunchy Bag Backend Updated Successfully!"
```

