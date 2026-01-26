# Jannenkoti Guide

Practical commands for running, maintaining, and migrating the system.

---

## 1) Environment

### 1.1 Activate venv
```bash
source .venv/bin/activate
```

### 1.2 Core env vars
```bash
export SECRET_KEY="..."
export DB_PATH="/opt/jannenkoti/jannenkoti.db"
export DATABASE_URL="postgresql+psycopg://jannesi:YOUR_PASS@localhost/jannenkoti"
export WEB_USERNAME="admin"
export WEB_PASSWORD="..."
```

Note: the app still uses SQLite runtime today, but Alembic migrations are Postgres-targeted.

---

## 2) Run the app

### 2.1 Local run (dev)
```bash
source .venv/bin/activate
python run.py
```

### 2.2 Restart service (repo helper)
```bash
./scripts/restart.sh
```

---

## 3) Postgres setup (self-hosted)

### 3.1 Install
```bash
sudo apt update
sudo apt install -y postgresql postgresql-contrib
```

### 3.2 Create user + DB
```bash
sudo -u postgres psql <<'SQL'
CREATE USER jannesi WITH PASSWORD 'CHANGE_ME_STRONG';
CREATE DATABASE jannenkoti OWNER jannesi;
GRANT ALL PRIVILEGES ON DATABASE jannenkoti TO jannesi;
SQL
```

### 3.3 Restrict to localhost
Edit `/etc/postgresql/*/main/postgresql.conf`:
```
listen_addresses = 'localhost'
```
Edit `/etc/postgresql/*/main/pg_hba.conf`:
```
host    all     all     127.0.0.1/32    scram-sha-256
host    all     all     ::1/128         scram-sha-256
```
Restart:
```bash
sudo systemctl restart postgresql
```

### 3.4 Install Python driver
```bash
source .venv/bin/activate
pip install "psycopg[binary]"
```

---

## 4) Alembic migrations (Postgres)

### 4.1 Point Alembic at Postgres
```bash
export DATABASE_URL="postgresql+psycopg://jannesi:YOUR_PASS@localhost/jannenkoti"
```

### 4.2 Run migrations
```bash
alembic upgrade head
```

### 4.3 Inspect state
```bash
alembic current
alembic heads
psql -U jannesi -h localhost -d jannenkoti -c '\dt'
```

---

## 5) SQLite (legacy runtime)

### 5.1 Quick checks
```bash
sqlite3 /opt/jannenkoti/jannenkoti.db ".tables"
sqlite3 /opt/jannenkoti/jannenkoti.db "SELECT * FROM car_heater_kfactor_result LIMIT 5;"
```

---

## 6) Backups

### 6.1 Postgres minimal backup
```bash
pg_dump -Fc -U jannesi -h localhost jannenkoti > /opt/backups/jannenkoti_$(date +%F).dump
```

### 6.2 SQLite backup
```bash
sqlite3 /opt/jannenkoti/jannenkoti.db ".backup '/opt/backups/jannenkoti_$(date +%F).db'"
```

---

## 7) Logs

### 7.1 Journal (systemd)
```bash
sudo journalctl -u jannenkoti -f --no-pager
```

---

## 8) Quick sanity checks

```bash
psql -U jannesi -h localhost -d jannenkoti -c "SELECT version_num FROM alembic_version;"
psql -U jannesi -h localhost -d jannenkoti -c "SELECT count(*) FROM car_heater_kfactor_session;"
```
