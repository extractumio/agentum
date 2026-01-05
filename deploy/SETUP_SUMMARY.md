# Agentum Quick Start - Web UI

## First Time Setup

```bash
# 1. Install Web UI dependencies
cd Project
./setup_web_ui.sh

# 2. Ensure Python venv is ready
source venv/bin/activate
pip install -r requirements.txt
```

## Running the Full Stack

### Option 1: VSCode (Recommended)

1. Press `F5` in VSCode
2. Select **"Full Stack (Backend + Web UI)"**
3. Browser opens automatically to http://localhost:50080

### Option 2: Manual

```bash
# Terminal 1 - Backend
cd Project && source venv/bin/activate
uvicorn src.api.main:app --host 0.0.0.0 --port 40080 --reload

# Terminal 2 - Frontend
cd Project/src/web_terminal_client
npm run dev
```

## Access URLs

| Service | URL |
|---------|-----|
| Web UI | http://localhost:50080 |
| Backend API | http://localhost:40080 |
| API Docs | http://localhost:40080/api/docs |

## VSCode Launch Configurations

| Configuration | Description |
|--------------|-------------|
| **Full Stack (Backend + Web UI)** | Runs both services together |
| **Backend API Server** | Runs only FastAPI backend |
| **Web UI (React/Vite)** | Runs only React frontend |
| **HTTP Agent Client** | Runs agent_http.py client |

## Documentation

- **Architecture**: `Project/docs/current_architecture.md` (Section 9: Web Terminal UI)
- **Development Guide**: `Project/docs/WEB_UI_DEVELOPMENT.md`

## Troubleshooting

```bash
# Kill processes on ports
lsof -ti:40080 | xargs kill -9  # Backend
lsof -ti:50080 | xargs kill -9  # Frontend

# Reinstall frontend dependencies
cd Project/src/web_terminal_client
rm -rf node_modules && npm install
```

