import subprocess
from pathlib import Path
from typing import Any

from .shared import _abs_path


def _run_command(cwd: str, argv: list[str]) -> dict[str, Any]:
    proc = subprocess.run(
        argv,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "command": " ".join(argv),
        "exit_code": int(proc.returncode),
        "stdout": (proc.stdout or "").strip(),
        "stderr": (proc.stderr or "").strip(),
    }


def update_gzchesupai_source_by_commit(
    commit: str,
    source_repo_root: str = "source/GZCheSuPaiApp",
) -> dict[str, Any]:
    """按固定串行步骤更新 GZCheSuPaiApp 源码仓库。

    执行顺序（固定）：
    1) git pull
    2) git submodule update
    3) git checkout <commit>
    """
    normalized_commit = str(commit or "").strip()
    if not normalized_commit:
        return {
            "ok": False,
            "error": "commit 不能为空。",
            "steps": [],
        }

    repo_path = Path(_abs_path(source_repo_root))
    if not repo_path.exists() or not repo_path.is_dir():
        return {
            "ok": False,
            "error": f"源码目录不存在: {repo_path}",
            "steps": [],
        }

    if not (repo_path / ".git").exists():
        return {
            "ok": False,
            "error": f"目录不是 git 仓库: {repo_path}",
            "steps": [],
        }

    commands = [
        ["git", "pull"],
        ["git", "submodule", "update"],
        ["git", "checkout", normalized_commit],
    ]

    step_results: list[dict[str, Any]] = []
    for argv in commands:
        step = _run_command(cwd=str(repo_path), argv=argv)
        step_results.append(step)
        if step["exit_code"] != 0:
            return {
                "ok": False,
                "repo_path": str(repo_path),
                "target_commit": normalized_commit,
                "failed_step": step["command"],
                "steps": step_results,
            }

    head_info = _run_command(cwd=str(repo_path), argv=["git", "rev-parse", "HEAD"])
    branch_info = _run_command(cwd=str(repo_path), argv=["git", "rev-parse", "--abbrev-ref", "HEAD"])

    return {
        "ok": True,
        "repo_path": str(repo_path),
        "target_commit": normalized_commit,
        "current_head": head_info.get("stdout", ""),
        "current_branch": branch_info.get("stdout", ""),
        "steps": step_results,
    }

