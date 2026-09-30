"""Git working copies: one branch and one isolated worktree per session.

The project's own checkout is never switched or modified. A session's first send creates
`checkpoint/<name>` from the project's base branch in a separate worktree under the data
folder, and every later task in that session lands there as commits. Merging is always the
user's call (a pull request, or a merge they run).
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..config import paths
from .naming import slug

log = logging.getLogger(__name__)

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class GitError(RuntimeError):
    pass


def git(args: list[str], cwd: str | Path, timeout: float = 60, check: bool = True) -> str:
    try:
        r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, creationflags=NO_WINDOW)
    except FileNotFoundError as e:
        raise GitError("Git isn't installed or isn't on PATH.") from e
    except subprocess.TimeoutExpired as e:
        raise GitError(f"git {args[0]} took too long") from e
    if check and r.returncode != 0:
        raise GitError((r.stderr or r.stdout).strip() or f"git {args[0]} failed")
    return r.stdout.strip()


@dataclass
class RepoInfo:
    root: str
    current_branch: str
    default_branch: str
    remote_url: str
    branches: list[str]


def inspect_repo(path: str) -> RepoInfo:
    p = Path(path or "").expanduser()
    if not p.is_dir():
        raise GitError("That folder doesn't exist.")
    try:
        root = git(["rev-parse", "--show-toplevel"], p)
    except GitError as e:
        raise GitError("That folder isn't a Git repository.") from e
    current = git(["branch", "--show-current"], root, check=False) or "HEAD"
    remote_head = git(["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"], root, check=False)
    default = remote_head.removeprefix("origin/") if remote_head else current
    branches = [b for b in git(["for-each-ref", "--format=%(refname:short)", "refs/heads"], root, check=False).splitlines() if b]
    remote = git(["remote", "get-url", "origin"], root, check=False)
    return RepoInfo(root=str(Path(root)), current_branch=current, default_branch=default, remote_url=remote, branches=branches)


def branch_exists(repo: str, name: str) -> bool:
    for ref in (f"refs/heads/{name}", f"refs/remotes/origin/{name}"):
        if git(["rev-parse", "--verify", "--quiet", ref], repo, check=False):
            return True
    return False


def valid_branch_name(repo: str, name: str) -> bool:
    return subprocess.run(["git", "check-ref-format", "--branch", name], cwd=repo, capture_output=True,
                          creationflags=NO_WINDOW).returncode == 0


def worktree_dir(project_name: str, branch: str) -> Path:
    return paths().root / "worktrees" / slug(project_name) / slug(branch.removeprefix("checkpoint/"))


def create_worktree(repo: str, branch: str, base: str, dest: Path) -> str:
    """New branch `branch` from `base`, checked out in `dest`. Returns the starting commit."""
    if not valid_branch_name(repo, branch):
        raise GitError(f"“{branch}” isn't a valid branch name")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and any(dest.iterdir()):
        raise GitError(f"The working copy folder already exists: {dest}")
    start = git(["rev-parse", "--verify", f"{base}^{{commit}}"], repo)
    git(["worktree", "add", "-b", branch, str(dest), start], repo, timeout=600)
    return start


def attach_worktree(repo: str, branch: str, dest: Path) -> None:
    """Re-create a missing working copy for an existing session branch (e.g. the folder was deleted)."""
    git(["worktree", "prune"], repo, check=False)
    dest.parent.mkdir(parents=True, exist_ok=True)
    git(["worktree", "add", str(dest), branch], repo, timeout=600)


def remove_worktree(repo: str, dest: str) -> None:
    git(["worktree", "remove", "--force", dest], repo, check=False, timeout=120)
    if Path(dest).exists():
        shutil.rmtree(dest, ignore_errors=True)
    git(["worktree", "prune"], repo, check=False)


def head(cwd: str) -> str:
    return git(["rev-parse", "HEAD"], cwd)


def current_branch(cwd: str) -> str:
    return git(["branch", "--show-current"], cwd, check=False)


def dirty_files(cwd: str) -> list[str]:
    out = git(["status", "--porcelain"], cwd, check=False)
    return [line[3:] for line in out.splitlines() if line.strip()]


def is_ancestor(cwd: str, older: str, newer: str) -> bool:
    return subprocess.run(["git", "merge-base", "--is-ancestor", older, newer], cwd=cwd, capture_output=True,
                          creationflags=NO_WINDOW).returncode == 0


def commits_since(cwd: str, base: str) -> list[dict]:
    if not base:
        return []
    out = git(["log", "--format=%H%x1f%s", f"{base}..HEAD"], cwd, check=False)
    rows = []
    for line in out.splitlines():
        if "\x1f" in line:
            sha, subject = line.split("\x1f", 1)
            rows.append({"sha": sha, "subject": subject})
    return rows


def task_numbers_in(subject: str) -> set[int]:
    return {int(n) for n in re.findall(r"\bT-(\d+)\b", subject)}


def web_url(remote: str) -> Optional[str]:
    """https URL of a GitHub/GitLab-style remote, for opening a pull request page."""
    r = remote.strip()
    m = re.match(r"git@([^:]+):(.+?)(\.git)?$", r)
    if m:
        return f"https://{m.group(1)}/{m.group(2)}"
    m = re.match(r"https?://(?:[^@/]+@)?(.+?)(\.git)?$", r)
    if m:
        return f"https://{m.group(1)}"
    return None


def gh_available() -> bool:
    return shutil.which("gh") is not None


def run_gh(args: list[str], cwd: str, timeout: float = 120) -> str:
    r = subprocess.run(["gh", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout, creationflags=NO_WINDOW, env={**os.environ, "GH_PROMPT_DISABLED": "1"})
    if r.returncode != 0:
        raise GitError((r.stderr or r.stdout).strip() or "gh failed")
    return r.stdout.strip()
