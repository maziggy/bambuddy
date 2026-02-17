# Contributing to Bambuddy

Thank you for your interest in contributing to Bambuddy! This document provides guidelines and instructions for contributing.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [Making Changes](#making-changes)
- [Code Style](#code-style)
- [Internationalization (i18n)](#internationalization-i18n)
- [Authentication & Permissions](#authentication--permissions)
- [Testing](#testing)
- [CI Pipeline](#ci-pipeline)
- [Submitting Changes](#submitting-changes)
- [Reporting Bugs](#reporting-bugs)
- [Requesting Features](#requesting-features)

## Code of Conduct

Please read and follow our [Code of Conduct](CODE_OF_CONDUCT.md) to keep our community welcoming and respectful.

## Getting Started

1. **Fork the repository** on GitHub
2. **Clone your fork** locally:
   ```bash
   git clone https://github.com/YOUR_USERNAME/bambuddy.git
   cd bambuddy
   ```
3. **Add the upstream remote**:
   ```bash
   git remote add upstream https://github.com/maziggy/bambuddy.git
   ```

## Development Setup

### Prerequisites

- Python 3.11+
- Node.js 20+
- npm

### Backend Setup

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt  # Dev/test dependencies (pytest, ruff, bandit, etc.)

# Install pre-commit hooks
pip install pre-commit
pre-commit install

# Run backend
DEBUG=true uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
```

### Frontend Setup

```bash
cd frontend

# Install dependencies
npm install

# Run development server
npm run dev
```

The frontend will be available at `http://localhost:5173` and will proxy API requests to the backend.

### Running with Docker

```bash
# Run the full application
docker compose up -d --build

# Run tests in Docker (mirrors CI)
docker compose -f docker-compose.test.yml run --rm backend-test
docker compose -f docker-compose.test.yml run --rm frontend-test
```

## Making Changes

1. **Create a branch** for your changes:
   ```bash
   git checkout -b feature/your-feature-name
   # or
   git checkout -b fix/your-bug-fix
   ```

2. **Make your changes** following our code style guidelines

3. **Test your changes** thoroughly

4. **Commit your changes** with clear, descriptive messages:
   ```bash
   git commit -m "Add feature: description of what you added"
   ```

### Branch Naming

- `feature/` - New features
- `fix/` - Bug fixes
- `docs/` - Documentation changes
- `refactor/` - Code refactoring
- `test/` - Test additions or fixes

## Code Style

### Backend (Python)

We use [Ruff](https://github.com/astral-sh/ruff) for linting and formatting. Configuration is in `pyproject.toml`.

```bash
# Check linting
ruff check backend/

# Auto-fix issues
ruff check --fix backend/

# Format code
ruff format backend/

# Check formatting without changes
ruff format --check backend/
```

### Frontend (TypeScript/React)

We use ESLint for linting and TypeScript for type checking:

```bash
cd frontend

# Lint
npm run lint

# Type check
npx tsc --noEmit
```

### Pre-commit Hooks

Pre-commit hooks run automatically on `git commit` and include Ruff linting/formatting, trailing whitespace fixes, YAML/JSON validation, and import shadowing checks. To run manually:

```bash
pre-commit run --all-files
```

## Internationalization (i18n)

The frontend uses [react-i18next](https://react.i18next.com/) for all user-facing text. **Never hardcode user-visible strings** — always use translation keys.

### Locale Files

Translations live in `frontend/src/i18n/locales/`:

| File | Language |
|------|----------|
| `en.ts` | English (primary) |
| `de.ts` | German |
| `fr.ts` | French |
| `ja.ts` | Japanese |

### Adding New Strings

1. Add the key to the appropriate section in **all three** locale files
2. Use the `useTranslation` hook in your component:

```tsx
import { useTranslation } from 'react-i18next';

function MyComponent() {
  const { t } = useTranslation();
  return <span>{t('section.myNewKey')}</span>;
}
```

3. Keys are organized by feature (e.g., `spoolman.`, `nav.`, `common.`)

### Important Notes

- All three locale files must use the **same key structure** — same nesting, same key paths
- Always add keys to all three locales to maintain parity
- Run frontend tests after changes — locale parity is validated
- If you find structural inconsistencies between locales, fix them — different key paths cause silent fallback to English

## Authentication & Permissions

Bambuddy has an optional authentication system. When auth is enabled, API endpoints are protected by granular permissions.

### How It Works

Authentication is **opt-in** — when disabled, all endpoints are open. The system uses `RequirePermissionIfAuthEnabled` which:

- Checks if auth is enabled in settings
- If disabled: allows the request through (no-op)
- If enabled: validates JWT token/API key and checks the user has the required permission

### Adding Auth to New Endpoints

Use the `RequirePermissionIfAuthEnabled` dependency in your route:

```python
from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.permissions import Permission

@router.get("/my-resource")
async def get_my_resource(
    _: User | None = RequirePermissionIfAuthEnabled(Permission.RESOURCE_READ),
):
    ...
```

### Permission Convention

Permissions follow the `resource:action` pattern (e.g., `filaments:read`, `printers:control`). Standard actions:

| Action | Usage |
|--------|-------|
| `read` | View/list resources |
| `create` | Create new resources |
| `update` | Modify existing resources |
| `delete` | Remove resources |

Some resources have additional actions (e.g., `printers:control` for start/stop, `printers:files` for file transfer).

### Adding New Permissions

1. Add the permission to the `Permission` enum in `backend/app/core/permissions.py`
2. Add it to the appropriate category in `PERMISSION_CATEGORIES`
3. Add it to the relevant default groups (`Administrators` gets all, `Operators` and `Viewers` as appropriate)
4. Use it in your route with `RequirePermissionIfAuthEnabled`

### Default Groups

| Group | Access Level |
|-------|-------------|
| **Administrators** | All permissions |
| **Operators** | Full control of printers, own items in archives/queue, read-only settings |
| **Viewers** | Read-only access to all resources |

## Testing

The easiest way to run tests is with the provided scripts in the project root:

```bash
./test_frontend.sh    # TypeScript check + ESLint + Vitest
./test_backend.sh     # Ruff lint/format + pytest (parallel)
./test_docker.sh      # Full Docker build, unit tests, and integration tests
./test_all.sh         # All of the above (frontend → backend → docker)
./test_security.sh    # Security scans (bandit, pip-audit, npm-audit)
```

`test_docker.sh` supports flags like `--backend-only`, `--skip-integration`, `--fresh` — run with `--help` for details.

`test_security.sh` runs fast scans by default. Use `--full` for the complete suite (CodeQL, Trivy, etc.) or specify individual scans like `./test_security.sh bandit codeql`.

### Running Tests Individually

**Backend** — tests are in `backend/tests/` with `unit/` and `integration/` subdirectories:

```bash
pytest backend/tests/ -v           # All tests
pytest backend/tests/unit/         # Unit tests only
pytest backend/tests/ --cov=backend  # With coverage
```

**Frontend** — tests use [Vitest](https://vitest.dev/) and are in `frontend/src/__tests__/`:

```bash
cd frontend
npm run test:run       # Single run
npm test               # Watch mode
npm run test:coverage  # With coverage
```

## CI Pipeline

Pull requests trigger automated CI checks via GitHub Actions (`.github/workflows/ci.yml`):

- **Backend**: Ruff lint + format check, unit/integration tests, pip-audit
- **Frontend**: ESLint, TypeScript type check, Vitest tests, production build
- **Docker**: Full image build, backend/frontend tests in Docker, integration health checks
- **Security**: CodeQL analysis, dependency audits

All checks must pass before merging. Run `./test_all.sh` locally before pushing to catch issues early.

## Submitting Changes

1. **Push your branch** to your fork:
   ```bash
   git push origin feature/your-feature-name
   ```

2. **Create a Pull Request** on GitHub:
   - Use a clear, descriptive title
   - Fill out the PR template completely
   - Link any related issues
   - Include screenshots for UI changes

3. **Wait for review** - maintainers will review your PR and may request changes

### PR Guidelines

- Keep PRs focused and reasonably sized
- One feature or fix per PR
- Update documentation if needed
- Add tests for new functionality
- Ensure all tests pass
- Follow the existing code style

## Reporting Bugs

Use the [Bug Report template](https://github.com/maziggy/bambuddy/issues/new?template=bug_report.yml) and include:

- Clear description of the bug
- Steps to reproduce
- Expected vs actual behavior
- Your environment (OS, Python version, browser)
- Printer model and firmware version
- Relevant logs

## Requesting Features

Use the [Feature Request template](https://github.com/maziggy/bambuddy/issues/new?template=feature_request.yml) and include:

- Clear description of the feature
- Use case / problem it solves
- Proposed solution
- Alternatives considered

## Questions?

- Check the [Documentation](http://wiki.bambuddy.cool)
- Open a [Discussion](https://github.com/maziggy/bambuddy/discussions)
- Review existing [Issues](https://github.com/maziggy/bambuddy/issues)

---

Thank you for contributing to Bambuddy!
