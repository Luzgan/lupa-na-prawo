#!/bin/bash
# EC2 setup script for Lupa na prawo
# Run as root on Amazon Linux 2023
# Assumes co-deployment with OpenBrain/LifeManager (Python 3.11, PostgreSQL, nginx already installed)

set -euo pipefail

echo "=== Setting up Lupa na prawo ==="
cd /home/ec2-user

if [ ! -d polish_law_helper ]; then
    echo "Clone the repo first:"
    echo "  git clone <repo-url> polish_law_helper"
    exit 1
fi

cd polish_law_helper

# --- Ollama ---
echo "=== Installing Ollama ==="
if ! command -v ollama &> /dev/null; then
    curl -fsSL https://ollama.com/install.sh | sh
fi
systemctl enable ollama
systemctl start ollama

echo "=== Pulling embedding model ==="
ollama pull jeffh/intfloat-multilingual-e5-large:f16

# --- Python ---
echo "=== Installing uv (if needed) ==="
if ! command -v uv &> /dev/null; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi

echo "=== Creating venv and installing deps ==="
uv venv .venv --python 3.12
uv sync

# --- Database ---
echo "=== Creating database (if needed) ==="
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname = 'polish_law'" | grep -q 1 || \
    sudo -u postgres psql -c "CREATE DATABASE polish_law"
sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname = 'app'" | grep -q 1 || \
    sudo -u postgres psql -c "CREATE USER app WITH PASSWORD 'CHANGE_ME'"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE polish_law TO app"
sudo -u postgres psql -d polish_law -c "CREATE EXTENSION IF NOT EXISTS vector"

echo "=== Running migrations ==="
source .venv/bin/activate
alembic upgrade head

# --- Ownership ---
echo "=== Fixing ownership ==="
chown -R ec2-user:ec2-user /home/ec2-user/polish_law_helper

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
