"""Tool: clone a public Git repository to a local temp directory."""
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# State shared across tool calls within a single agent session
_clone_state: dict[str, Any] = {}


def clone_repository(repo_url: str, include_tests: bool = False) -> dict[str, Any]:
    """
    Clone a public Git repository and return metadata about the Java project.

    Args:
        repo_url: HTTPS URL of the public Git repository (e.g. https://github.com/org/repo).
        include_tests: If True, include src/test directories in the scan.

    Returns:
        dict with keys: repo_dir, repo_name, branch, java_files (list of paths),
        java_file_count, total_size_mb, included_tests, error (if any).
    """
    import git  # gitpython

    # Clean up any previous clone
    prev_dir = _clone_state.get("repo_dir")
    if prev_dir and os.path.isdir(prev_dir):
        try:
            shutil.rmtree(prev_dir)
            logger.info(f"Removed previous clone at {prev_dir}")
        except Exception as e:
            logger.warning(f"Could not remove previous clone: {e}")

    repo_name = repo_url.rstrip("/").split("/")[-1].removesuffix(".git")
    clone_dir = tempfile.mkdtemp(prefix=f"java_scan_{repo_name}_")

    logger.info(f"Cloning {repo_url} into {clone_dir}")
    try:
        repo = git.Repo.clone_from(repo_url, clone_dir, depth=1)
        branch = repo.active_branch.name if repo.head.is_detached is False else "HEAD"
    except Exception as e:
        shutil.rmtree(clone_dir, ignore_errors=True)
        return {"error": f"Clone failed: {e}", "repo_url": repo_url}

    # Walk for Java files
    java_files = _find_java_files(clone_dir, include_tests=include_tests)

    # Rough size estimate
    total_bytes = sum(
        os.path.getsize(os.path.join(dp, f))
        for dp, _, fnames in os.walk(clone_dir)
        for f in fnames
    )
    total_mb = round(total_bytes / (1024 * 1024), 2)

    result = {
        "repo_url": repo_url,
        "repo_name": repo_name,
        "repo_dir": clone_dir,
        "branch": branch,
        "java_files": java_files,
        "java_file_count": len(java_files),
        "total_size_mb": total_mb,
        "included_tests": include_tests,
    }

    # Persist for downstream tools
    _clone_state.update(result)
    logger.info(f"Cloned {repo_name}: {len(java_files)} Java files, {total_mb} MB")
    return result


def _find_java_files(root: str, include_tests: bool) -> list[str]:
    """Return relative paths of all .java files under root."""
    root_path = Path(root)
    test_patterns = {"src/test", "src/androidTest", "src/integrationTest"}
    found = []
    for path in root_path.rglob("*.java"):
        rel = path.relative_to(root_path).as_posix()
        if not include_tests:
            if any(rel.startswith(p) for p in test_patterns):
                continue
        found.append(rel)
    return sorted(found)


def get_clone_state() -> dict[str, Any]:
    """Return the current clone state (for use by other tools)."""
    return _clone_state
