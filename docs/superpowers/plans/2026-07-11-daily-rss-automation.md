# Daily RSS Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 每天北京时间 08:00 由 GitHub Actions 增量更新全部 RSS，将安全通过的数据库直接提交到 `main`，并显式刷新 GitHub Pages。

**Architecture:** 新增一个最小权限、串行运行的 GitHub Actions 工作流。每次从 `docs/data/papers.db` 恢复被忽略的工作数据库，调用现有 `paper-radar update` 完成抓取、校验和原子发布；仅在公开数据库变化时提交，随后无论是否变化都调用 Pages Build API。仓库测试以结构化 YAML 和命令顺序合同锁定时间、权限、提交范围、无强推和失败安全边界。

**Tech Stack:** GitHub Actions, YAML, Ubuntu runner, Python 3.12, existing `paper-radar` CLI, SQLite, pytest, PyYAML, GitHub CLI/API, GitHub Pages.

---

## File map

- Create: `.github/workflows/daily-rss-update.yml` — 云端定时、手动触发、增量更新、提交和 Pages 刷新。
- Create: `tests/test_daily_workflow.py` — 工作流结构、安全权限、顺序、提交范围和 Secret 合同。
- Modify: `README.md` — 面向用户说明自动更新时间、云端运行、手动补跑、失败排查和可选 Secret。
- Modify: `tests/test_release_files.py` — 锁定 README 自动更新说明与工作流路径。

固定的官方 Action 版本（2026-07-11 查询 GitHub 官方 tag）：

- `actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0` (`v7.0.0`)
- `actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1` (`v6.3.0`)

### Task 1: Add a tested daily update workflow

**Files:**
- Create: `.github/workflows/daily-rss-update.yml`
- Create: `tests/test_daily_workflow.py`

- [ ] **Step 1: Write the failing workflow structure tests**

Create `tests/test_daily_workflow.py`:

```python
from __future__ import annotations

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "daily-rss-update.yml"
CHECKOUT_SHA = "9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0"
SETUP_PYTHON_SHA = "ece7cb06caefa5fff74198d8649806c4678c61a1"


def _load() -> tuple[str, dict[str, object]]:
    text = WORKFLOW.read_text(encoding="utf-8")
    parsed = yaml.load(text, Loader=yaml.BaseLoader)
    assert isinstance(parsed, dict)
    return text, parsed


def _job(parsed: dict[str, object]) -> dict[str, object]:
    jobs = parsed["jobs"]
    assert isinstance(jobs, dict)
    job = jobs["update"]
    assert isinstance(job, dict)
    return job


def _steps(job: dict[str, object]) -> list[dict[str, str]]:
    raw_steps = job["steps"]
    assert isinstance(raw_steps, list)
    steps: list[dict[str, str]] = []
    for raw_step in raw_steps:
        assert isinstance(raw_step, dict)
        steps.append({str(key): str(value) for key, value in raw_step.items()})
    return steps


def test_daily_workflow_has_exact_triggers_permissions_and_concurrency() -> None:
    _, parsed = _load()
    triggers = parsed["on"]
    assert isinstance(triggers, dict)
    schedule = triggers["schedule"]
    assert schedule == [{"cron": "0 0 * * *"}]
    assert "workflow_dispatch" in triggers
    assert parsed["permissions"] == {"contents": "write", "pages": "write"}
    assert parsed["concurrency"] == {
        "group": "daily-rss-update",
        "cancel-in-progress": "false",
    }
    job = _job(parsed)
    assert job["runs-on"] == "ubuntu-latest"
    assert job["timeout-minutes"] == "30"


def test_daily_workflow_pins_actions_and_uses_optional_unpaywall_secret() -> None:
    text, parsed = _load()
    job = _job(parsed)
    steps = _steps(job)
    uses = [step["uses"] for step in steps if "uses" in step]
    assert uses == [
        f"actions/checkout@{CHECKOUT_SHA}",
        f"actions/setup-python@{SETUP_PYTHON_SHA}",
    ]
    assert "fetch-depth: 0" in text
    assert "python-version: '3.12'" in text
    assert job["env"] == {"UNPAYWALL_EMAIL": "${{ secrets.UNPAYWALL_EMAIL }}"}
    assert "@main" not in text
    assert not re.search(r"uses:\s+[^\s]+@(v?\d+(?:\.\d+)*)\s*$", text, re.MULTILINE)
```

- [ ] **Step 2: Run the structure tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_daily_workflow.py -v
```

Expected: FAIL with `FileNotFoundError` for `.github/workflows/daily-rss-update.yml`.

- [ ] **Step 3: Add failing data-flow and safety tests**

Append to `tests/test_daily_workflow.py`:

```python
def test_daily_workflow_restores_then_updates_and_limits_change_scope() -> None:
    text, parsed = _load()
    steps = _steps(_job(parsed))
    runs = "\n".join(step.get("run", "") for step in steps)
    restore_at = runs.index("cp docs/data/papers.db data/papers.db")
    update_at = runs.index("python -m paper_radar update")
    scope_at = runs.index("git diff --name-only")
    assert restore_at < update_at < scope_at
    assert "mkdir -p data" in runs
    assert "python -m pip install ." in runs
    assert 'tee "$RUNNER_TEMP/update-result.json"' in runs
    assert "grep -vFx 'docs/data/papers.db'" in runs
    assert "git add -- docs/data/papers.db" in runs
    assert "git diff --cached --quiet" in runs
    assert "git commit -m \"chore(data): daily RSS update\"" in runs
    assert "git pull --rebase origin main" in runs
    assert "git push origin HEAD:main" in runs
    assert "--force" not in runs
    assert "git add ." not in runs


def test_daily_workflow_skips_empty_commit_and_always_requests_pages_build() -> None:
    _, parsed = _load()
    steps = _steps(_job(parsed))
    by_name = {step["name"]: step for step in steps if "name" in step}
    detect = by_name["Detect database change"]
    commit = by_name["Commit database update"]
    pages = by_name["Request GitHub Pages build"]
    assert detect["id"] == "changes"
    assert 'database_changed=false' in detect["run"]
    assert 'database_changed=true' in detect["run"]
    assert commit["if"] == "steps.changes.outputs.database_changed == 'true'"
    assert "if" not in pages
    assert "gh api --method POST" in pages["run"]
    assert 'repos/${GITHUB_REPOSITORY}/pages/builds' in pages["run"]
    assert pages["env"] == "{'GH_TOKEN': '${{ github.token }}'}"


def test_daily_workflow_has_no_personal_token_or_hardcoded_email() -> None:
    text, _ = _load()
    lowered = text.lower()
    assert "personal_access_token" not in lowered
    assert "github_pat" not in lowered
    assert "@example." not in lowered
    assert "@gmail." not in lowered
    assert "@qq." not in lowered
    assert "secrets.unpaywall_email" in lowered
    assert "github.token" in lowered
```

- [ ] **Step 4: Create the minimal complete workflow**

Create `.github/workflows/daily-rss-update.yml` with this exact content:

```yaml
name: Daily RSS Update

on:
  schedule:
    - cron: "0 0 * * *"
  workflow_dispatch:

permissions:
  contents: write
  pages: write

concurrency:
  group: daily-rss-update
  cancel-in-progress: false

jobs:
  update:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    env:
      UNPAYWALL_EMAIL: ${{ secrets.UNPAYWALL_EMAIL }}

    steps:
      - name: Check out repository
        uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7.0.0
        with:
          fetch-depth: 0

      - name: Set up Python
        uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1 # v6.3.0
        with:
          python-version: '3.12'
          cache: pip

      - name: Install application
        run: |
          set -euo pipefail
          python -m pip install --upgrade pip
          python -m pip install .

      - name: Restore incremental working database
        run: |
          set -euo pipefail
          mkdir -p data
          cp docs/data/papers.db data/papers.db

      - name: Update RSS database
        run: |
          set -euo pipefail
          python -m paper_radar update | tee "$RUNNER_TEMP/update-result.json"

      - name: Reject unexpected tracked changes
        run: |
          set -euo pipefail
          unexpected="$(git diff --name-only | grep -vFx 'docs/data/papers.db' || true)"
          if [[ -n "$unexpected" ]]; then
            echo "Unexpected tracked changes:" >&2
            echo "$unexpected" >&2
            exit 1
          fi

      - name: Detect database change
        id: changes
        run: |
          if git diff --quiet -- docs/data/papers.db; then
            echo "database_changed=false" >> "$GITHUB_OUTPUT"
          else
            echo "database_changed=true" >> "$GITHUB_OUTPUT"
          fi

      - name: Write update summary
        run: |
          {
            echo "### Daily RSS update"
            echo "- Trigger: $GITHUB_EVENT_NAME"
            echo "- Database changed: ${{ steps.changes.outputs.database_changed }}"
            echo "- Result:"
            echo '```json'
            cat "$RUNNER_TEMP/update-result.json"
            echo '```'
          } >> "$GITHUB_STEP_SUMMARY"

      - name: Commit database update
        if: steps.changes.outputs.database_changed == 'true'
        run: |
          set -euo pipefail
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add -- docs/data/papers.db
          if git diff --cached --quiet; then
            echo "No staged database change; skipping commit."
            exit 0
          fi
          git commit -m "chore(data): daily RSS update"
          git pull --rebase origin main
          git push origin HEAD:main

      - name: Request GitHub Pages build
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          set -euo pipefail
          gh api --method POST "repos/${GITHUB_REPOSITORY}/pages/builds"
```

- [ ] **Step 5: Run the workflow tests and verify GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_daily_workflow.py -v
```

Expected: all tests PASS.

- [ ] **Step 6: Run focused release tests and lint**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_daily_workflow.py tests/test_release_files.py tests/test_cli.py -v
.\.venv\Scripts\python.exe -m ruff check tests/test_daily_workflow.py
git diff --check
```

Expected: all tests PASS; Ruff and `git diff --check` exit 0.

- [ ] **Step 7: Commit the tested workflow**

```powershell
git add .github/workflows/daily-rss-update.yml tests/test_daily_workflow.py
git commit -m "feat: automate daily RSS updates"
```

### Task 2: Document cloud automation and operator recovery

**Files:**
- Modify: `README.md`
- Modify: `tests/test_release_files.py`

- [ ] **Step 1: Write the failing documentation contract**

Append to `tests/test_release_files.py`:

```python
def test_readme_documents_daily_cloud_update_and_recovery() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    workflow = ROOT / ".github" / "workflows" / "daily-rss-update.yml"

    assert workflow.is_file()
    for phrase in (
        "每天北京时间 08:00",
        "不需要打开 Codex",
        "不需要保持本地电脑开机",
        "Daily RSS Update",
        "Run workflow",
        "UNPAYWALL_EMAIL",
        "连续 60 天",
        "docs/data/papers.db",
    ):
        assert phrase in readme
```

- [ ] **Step 2: Run the documentation contract and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_release_files.py::test_readme_documents_daily_cloud_update_and_recovery -v
```

Expected: FAIL because the README does not yet contain `每天北京时间 08:00`.

- [ ] **Step 3: Add the exact README automation section**

Insert after the current “手动更新与安全发布” section:

```markdown
## 3. 每日云端自动更新

仓库通过 GitHub Actions 工作流 **Daily RSS Update**，每天北京时间 08:00 自动检查全部已启用 RSS。任务在 GitHub 的云端 runner 中执行，不需要打开 Codex，也不需要保持本地电脑开机或联网。

自动任务从当前 `docs/data/papers.db` 恢复增量工作数据库，通过与手动更新相同的抓取、校验和发布闸门。有数据变化时，GitHub Actions 机器人只提交 `docs/data/papers.db`；没有变化时不创建空提交。任务最后显式请求 GitHub Pages 构建。

手动补跑：打开仓库 **Actions → Daily RSS Update → Run workflow**。运行日志和 Job Summary 会显示新增、更新、跳过、失败来源以及是否产生数据库提交。

Unpaywall 邮箱是可选项。如需启用，在仓库 **Settings → Secrets and variables → Actions** 添加名为 `UNPAYWALL_EMAIL` 的 Secret；不要把邮箱写进工作流、`.env.example` 以外的公开文件或提交历史。未设置时 RSS 和 Crossref 更新仍会运行，无法确认的 OA 状态保持 `unknown`。

公开仓库连续 60 天没有活动时，GitHub 可能自动停用定时工作流。如果自动更新停止，在 Actions 页面重新启用 **Daily RSS Update**，再点击 **Run workflow** 补跑。自动任务失败不会删除或覆盖当前已发布网站；先检查失败步骤，再手动补跑。
```

Renumber the existing headings without changing their substantive content:

```text
3 → 4  本地预览
4 → 5  运行全部测试
5 → 6  添加或修改期刊
6 → 7  添加主题标签
7 → 8  两轮本地验收清单
8 → 9  手动发布到 GitHub Pages
9 → 10 隐私、版权与访问边界
10 → 11 常见故障
11 → 12 目录结构
```

- [ ] **Step 4: Run release documentation tests and verify GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_release_files.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit the documentation**

```powershell
git add README.md tests/test_release_files.py
git commit -m "docs: explain daily RSS automation"
```

### Task 3: Run the full repository quality gates

**Files:**
- Verify only: complete repository

- [ ] **Step 1: Run all Python and Playwright tests**

```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

Expected: all tests PASS with zero failures and no browser skip under normal acceptance.

- [ ] **Step 2: Run Node and Ruff gates**

```powershell
$node = 'C:\Users\Xuanqi\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'
$files = Get-ChildItem tests\web -Filter *.test.mjs | Sort-Object Name | Select-Object -ExpandProperty FullName
& $node --test $files
.\.venv\Scripts\python.exe -m ruff check .
git diff --check
```

Expected: 19 Node tests PASS; Ruff and `git diff --check` exit 0.

- [ ] **Step 3: Verify workflow scope and repository state**

```powershell
git status --short
git log -4 --oneline
```

Expected: worktree clean; the two automation commits are present after the plan commit.

### Task 4: Publish, manually trigger, and verify the first cloud run

**Files:**
- External state: `Hsuanqi77/academic-rss-site` `main`, GitHub Actions, GitHub Pages

- [ ] **Step 1: Push the verified automation commits**

After explicit user approval for the default-branch push:

```powershell
git push origin main
```

Expected: remote `main` advances without force push or rejected updates.

- [ ] **Step 2: Confirm GitHub recognizes the workflow and trigger it manually**

```powershell
$gh = 'C:\Program Files\GitHub CLI\gh.exe'
& $gh workflow view daily-rss-update.yml --repo Hsuanqi77/academic-rss-site
& $gh workflow run daily-rss-update.yml --repo Hsuanqi77/academic-rss-site --ref main
```

Expected: workflow metadata is displayed and `workflow run` exits 0.

- [ ] **Step 3: Identify and watch the manual run**

```powershell
$gh = 'C:\Program Files\GitHub CLI\gh.exe'
$runId = & $gh run list --repo Hsuanqi77/academic-rss-site --workflow daily-rss-update.yml --event workflow_dispatch --limit 1 --json databaseId --jq '.[0].databaseId'
if (-not $runId) { throw 'No manual Daily RSS Update run was found' }
& $gh run watch $runId --repo Hsuanqi77/academic-rss-site --exit-status
```

Expected: the run reaches `completed/success`. If it fails, inspect `gh run view $runId --log-failed`, fix the actual cause, rerun tests, and repeat the manual trigger; do not weaken database validation or force push.

- [ ] **Step 4: Verify any bot database commit is properly scoped**

```powershell
$gh = 'C:\Program Files\GitHub CLI\gh.exe'
$remoteSha = & $gh api repos/Hsuanqi77/academic-rss-site/commits/main --jq .sha
$remoteMessage = & $gh api repos/Hsuanqi77/academic-rss-site/commits/main --jq .commit.message
$remoteFiles = & $gh api repos/Hsuanqi77/academic-rss-site/commits/$remoteSha --jq '.files[].filename'
[pscustomobject]@{Sha=$remoteSha;Message=$remoteMessage;Files=($remoteFiles -join ',')}
```

Expected outcomes:

- If the message is `chore(data): daily RSS update`, the only changed file is `docs/data/papers.db`.
- If no database change was detected, `main` remains at the workflow deployment commit and no empty bot commit exists.

- [ ] **Step 5: Verify Pages build and production database**

```powershell
$gh = 'C:\Program Files\GitHub CLI\gh.exe'
$deadline = (Get-Date).AddMinutes(5)
do {
  $pages = & $gh api repos/Hsuanqi77/academic-rss-site/pages/builds/latest | ConvertFrom-Json
  if ($pages.status -eq 'built') { break }
  if ($pages.status -eq 'errored') { throw 'GitHub Pages build failed' }
  Start-Sleep -Seconds 5
} until ((Get-Date) -ge $deadline)
if ($pages.status -ne 'built') { throw "Pages build timed out with $($pages.status)" }

$site = 'https://hsuanqi77.github.io/academic-rss-site'
$tempDatabase = Join-Path $env:TEMP "papers-$($pages.commit).db"
try {
  Invoke-WebRequest -UseBasicParsing "$site/data/papers.db?verify=$($pages.commit)" -OutFile $tempDatabase
  $bytes = [System.IO.File]::ReadAllBytes($tempDatabase)
  $signature = [System.Text.Encoding]::ASCII.GetString($bytes, 0, 16)
  if ($signature -ne "SQLite format 3`0") {
    throw 'Production database is not SQLite schema data'
  }
  [pscustomobject]@{PagesCommit=$pages.commit;DatabaseBytes=$bytes.Length}
} finally {
  Remove-Item -LiteralPath $tempDatabase -Force -ErrorAction SilentlyContinue
}
```

Expected: Pages is built from the latest requested revision and the production database returns HTTP 200 with the SQLite signature.

- [ ] **Step 6: Verify schedule and production site behavior**

```powershell
$gh = 'C:\Program Files\GitHub CLI\gh.exe'
$encoded = & $gh api repos/Hsuanqi77/academic-rss-site/contents/.github/workflows/daily-rss-update.yml --jq .content
$workflowText = [System.Text.Encoding]::UTF8.GetString(
  [System.Convert]::FromBase64String(($encoded -replace '\s', ''))
)
if ($workflowText -notmatch 'cron:\s+"0 0 \* \* \*"') {
  throw 'Production workflow does not contain the approved daily cron'
}
"Verified cron: 0 0 * * * (08:00 Asia/Shanghai)"
```

Open <https://hsuanqi77.github.io/academic-rss-site/> and verify articles load, search/filter still work, and the data-status section shows a successful published snapshot.
