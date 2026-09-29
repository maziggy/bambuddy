# Maintainer Guide

This document provides setup instructions for repository maintainers.

## Branch Protection Setup

To protect the `main` branch, go to **Settings > Rules > Rulesets > New ruleset > New branch ruleset**.

### Step 1: Basic Settings

| Field | Value |
|-------|-------|
| Ruleset name | `Protect main` |
| Enforcement status | `Active` |

### Step 2: Bypass List (optional)

Add yourself (`@maziggy`) to bypass if you want to push directly in emergencies.
Set "Always" or "Pull requests only" based on preference.

### Step 3: Target Branches

Click **Add target** > **Include by pattern** and enter: `main`

### Step 4: Branch Rules

Enable these rules:

**Restrict deletions** - Prevents branch deletion

**Require a pull request before merging**
- Required approvals: `1`
- [x] Dismiss stale pull request approvals when new commits are pushed
- [ ] Require review from Code Owners (optional)
- [x] Require approval of the most recent reviewable push

**Require status checks to pass**
- [x] Require branches to be up to date before merging
- Add these status checks (they appear after CI runs once):
  - `Backend Lint`
  - `Backend Tests`
  - `Frontend Lint`
  - `Frontend Type Check`
  - `Frontend Tests`
  - `Frontend Build`
  - `Docker Build`

**Block force pushes** - Prevents history rewriting

### Optional (stricter)

- [ ] Require conversation resolution before merging
- [ ] Require signed commits
- [ ] Require linear history

## CI Workflow

The CI workflow (`.github/workflows/ci.yml`) runs on:
- All pull requests to `main`
- All pushes to `main`

### Jobs

| Job | Purpose | Required for PR |
|-----|---------|-----------------|
| `backend-lint` | Ruff linting + format check | Yes |
| `backend-tests` | Unit tests | Yes |
| `frontend-lint` | ESLint | Yes |
| `frontend-typecheck` | TypeScript compilation | Yes |
| `frontend-tests` | Vitest unit tests | Yes |
| `frontend-build` | Vite production build | Yes |
| `docker-build` | Docker image builds | Yes |

### Fixing CI Failures

**Backend lint failures:**
```bash
ruff check --fix backend/
ruff format backend/
```

**Frontend lint failures:**
```bash
cd frontend
npm run lint -- --fix
```

**Frontend type errors:**
```bash
cd frontend
npm run typecheck
# Fix the errors shown
```

**Frontend test failures:**
```bash
cd frontend
npm run test:run
# Fix failing tests
```

## CODEOWNERS

The `CODEOWNERS` file automatically requests reviews from `@maziggy` for all changes.

To add more code owners:
1. Edit `.github/CODEOWNERS`
2. Add GitHub usernames with `@` prefix
3. Assign specific paths to specific owners

Example:
```
/backend/ @maziggy @backend-contributor
/frontend/ @maziggy @frontend-contributor
```

## Release Process

1. Update version in `pyproject.toml`
2. Update `CHANGELOG.md`
3. If Bambu Studio has updated its HMS files since the last release, run
   `python scripts/generate_hms_catalog.py /path/to/BambuStudio` and review the
   diff of `backend/app/data/hms_catalog.json` (added codes, changed texts, codes
   that lost their text)
4. Create a PR with these changes
5. After merge, tag the release:
   ```bash
   git tag v0.1.x
   git push origin v0.1.x
   ```
6. Run `docker-publish.sh` to publish Docker image

## Dependabot (Optional)

To enable automated dependency updates, create `.github/dependabot.yml`:

```yaml
version: 2
updates:
  - package-ecosystem: "pip"
    directory: "/"
    schedule:
      interval: "weekly"
    groups:
      python-dependencies:
        patterns:
          - "*"

  - package-ecosystem: "npm"
    directory: "/frontend"
    schedule:
      interval: "weekly"
    groups:
      npm-dependencies:
        patterns:
          - "*"

  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: "weekly"
```
