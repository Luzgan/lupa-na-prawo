#!/bin/bash
# EC2 setup script for Lupa na prawo
# Run as root on Amazon Linux 2023
# Assumes co-deployment with OpenBrain/LifeManager (Python 3.11, PostgreSQL, nginx already installed)

set -euo pipefail

echo "=== Setting up Lupa na prawo ==="
cd /home/ec2-user

if [ ! -d lupa-na-prawo ]; then
    echo "Clone the repo first:"
    echo "  git clone <repo-url> lupa-na-prawo"
    exit 1
fi

cd lupa-na-prawo

# --- Ollama ---
echo "=== Installing Ollama ==="
if ! command -v ollama &> /dev/null; then
    curl -fsSL https://ollama.com/install.sh | sh
fi

if [ ! -f /etc/systemd/system/ollama.service ]; then
    echo "=== Creating Ollama systemd service ==="
    cat > /etc/systemd/system/ollama.service << 'UNIT'
[Unit]
Description=Ollama
After=network.target

[Service]
Type=simple
Environment=HOME=/home/ec2-user
ExecStart=/usr/local/bin/ollama serve
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
    systemctl daemon-reload
fi

systemctl enable ollama
systemctl start ollama

echo "=== Waiting for Ollama to be ready ==="
for i in $(seq 1 30); do
    ollama list &> /dev/null && break
    sleep 1
done

echo "=== Pulling embedding model ==="
ollama pull jeffh/intfloat-multilingual-e5-large:f16

# --- Python ---
echo "=== Installing uv (if needed) ==="
export PATH="/home/ec2-user/.local/bin:/root/.local/bin:$PATH"
if ! command -v uv &> /dev/null; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # Make uv available system-wide
    ln -sf /root/.local/bin/uv /usr/local/bin/uv
    ln -sf /root/.local/bin/uvx /usr/local/bin/uvx
fi

echo "=== Creating venv and installing deps ==="
uv venv .venv --python 3.12
uv sync

# --- PostgreSQL ---
echo "=== Installing PostgreSQL (if needed) ==="
if ! command -v psql &> /dev/null; then
    dnf install -y postgresql17 postgresql17-server postgresql17-server-devel
    postgresql-setup --initdb
    systemctl enable postgresql
    systemctl start postgresql
fi

echo "=== Setting up database ==="
sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname = 'app'" | grep -q 1 || \
    sudo -u postgres psql -c "CREATE USER app WITH PASSWORD 'CHANGE_ME'"
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname = 'polish_law'" | grep -q 1 || \
    sudo -u postgres psql -c "CREATE DATABASE polish_law OWNER app"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE polish_law TO app"
sudo -u postgres psql -d polish_law -c "CREATE EXTENSION IF NOT EXISTS vector"

# Allow local password auth (idempotent — only changes peer/ident if present)
PG_HBA=$(sudo -u postgres psql -t -c "SHOW hba_file;" | xargs)
sed -i '/^local.*all.*all/s/peer/md5/' "$PG_HBA"
sed -i '/^host.*all.*all.*127/s/ident/md5/' "$PG_HBA"
systemctl restart postgresql

echo "=== Running migrations ==="
source .venv/bin/activate
alembic upgrade head

# --- Ownership ---
echo "=== Fixing ownership ==="
chown -R ec2-user:ec2-user /home/ec2-user/lupa-na-prawo

# --- systemd ---
echo "=== Installing systemd service ==="
cp deploy/systemd/lupa-na-prawo.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable lupa-na-prawo

# --- nginx ---
echo "=== Setting up nginx reverse proxy ==="
cp deploy/nginx/lupa-na-prawo.conf /etc/nginx/conf.d/
nginx -t
systemctl restart nginx

echo ""
echo "=== Done! Next steps ==="
echo "1. Create .env file:"
echo "   PLH_DATABASE_URL=postgresql+asyncpg://app:CHANGE_ME@localhost:5432/polish_law"
echo "   PLH_DATABASE_URL_SYNC=postgresql+psycopg://app:CHANGE_ME@localhost:5432/polish_law"
echo "   PLH_OLLAMA_URL=http://localhost:11434"
echo "   PLH_BASE_URL=https://lupa-na-prawo.lukholc.me"
echo "2. Generate SSL cert:"
echo "   sudo certbot certonly --standalone -d lupa-na-prawo.lukholc.me"
echo "   (stop nginx first: sudo systemctl stop nginx)"
echo "3. sudo systemctl start lupa-na-prawo"
echo "4. Run initial ingestion:"
echo "   source .venv/bin/activate"
echo "   plh ingest-acts && plh ingest-sejm && plh ingest-senat"
echo "5. sudo journalctl -u lupa-na-prawo -f"
