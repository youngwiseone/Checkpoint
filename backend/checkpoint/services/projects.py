"""Project settings, shared by the API and the Shift+F9 project switcher."""

from __future__ import annotations

from ..models import Project

AGENTS = ("none", "claude", "codex")
ACCESS = ("standard", "full")
AGENT_LABEL = {"none": "No agent", "claude": "Claude Code", "codex": "Codex"}
FIELDS = ("name", "description", "glossary", "archived", "repo_path", "base_branch", "default_agent", "agent_access",
          "setup_command", "check_command", "preview_command", "preview_url")


class ProjectError(ValueError):
    pass


def apply_patch(p: Project, patch: dict) -> None:
    """Validate and apply changed settings (None means unchanged). A new repo folder clears the old base branch."""
    patch = {k: v for k, v in patch.items() if k in FIELDS and v is not None}
    if patch.get("default_agent") not in (None, *AGENTS):
        raise ProjectError("Unknown agent")
    if patch.get("agent_access") not in (None, *ACCESS):
        raise ProjectError("Unknown access level")
    if patch.get("repo_path"):
        from .workspace import GitError, inspect_repo

        try:
            patch["repo_path"] = inspect_repo(patch["repo_path"]).root
        except GitError as e:
            raise ProjectError(str(e)) from e
        if patch["repo_path"] != p.repo_path and "base_branch" not in patch:
            patch["base_branch"] = ""  # a branch of the old repo means nothing in the new one
    for k, v in patch.items():
        setattr(p, k, (v.strip() or None) if k in ("repo_path", "base_branch") and isinstance(v, str)
                else (v.strip() if isinstance(v, str) else v))
    if not p.name:
        raise ProjectError("Name can't be empty")
