"""Safety-checked file search tool for the SoC agent.

`search_files` finds files whose names match a glob pattern inside a folder
(mode="files"), or greps file contents for a regex pattern (mode="content").
Read-only; returns a JSON ToolResult.
"""

import fnmatch
import os
import re

from safety import (
    ALLOWED_KNOWLEDGE_ROOTS,
    ALLOWED_PROJECT_ROOTS,
    SecurityError,
    ToolResult,
    validate_path,
)

_ALLOWED_ROOTS = tuple(ALLOWED_PROJECT_ROOTS) + tuple(ALLOWED_KNOWLEDGE_ROOTS)

MAX_RESULTS = 200
MAX_LINE_CHARS = 200


def search_files(folder: str, pattern: str, mode: str = "files") -> str:
    """Search for files in a folder: name matches (mode="files") or content greps (mode="content")."""
    action = "search_files"
    try:
        safe_folder = validate_path(
            folder, _ALLOWED_ROOTS, must_exist=False, label="folder"
        )
        if not os.path.isdir(safe_folder):
            raise FileNotFoundError(f"Folder not found: '{safe_folder}'")
        if not isinstance(pattern, str) or not pattern.strip():
            raise ValueError("'pattern' must be a non-empty string.")
        mode = str(mode or "files").lower()
        if mode not in ("files", "content"):
            raise ValueError("'mode' must be 'files' or 'content'.")
        if mode == "content":
            try:
                rx = re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"Invalid pattern: {exc}")

        matches = []
        for root, dirs, files in os.walk(safe_folder):
            dirs.sort()
            for name in sorted(files):
                path = os.path.join(root, name)
                if mode == "files":
                    if fnmatch.fnmatch(name, pattern):
                        matches.append({"path": path, "name": name})
                        if len(matches) >= MAX_RESULTS:
                            break
                else:
                    try:
                        with open(
                            path, "r", encoding="utf-8", errors="ignore", newline=""
                        ) as handle:
                            for lineno, line in enumerate(handle, 1):
                                if rx.search(line):
                                    matches.append(
                                        {
                                            "path": path,
                                            "line": lineno,
                                            "text": line.rstrip("\r\n")[:MAX_LINE_CHARS],
                                        }
                                    )
                                    if len(matches) >= MAX_RESULTS:
                                        break
                    except OSError:
                        continue
                if len(matches) >= MAX_RESULTS:
                    break
            if len(matches) >= MAX_RESULTS:
                break

        return ToolResult(
            ok=True,
            action=action,
            status="ok",
            data={
                "folder": safe_folder,
                "pattern": pattern,
                "mode": mode,
                "matches": matches,
                "count": len(matches),
            },
        ).to_json()
    except (SecurityError, FileNotFoundError, OSError, ValueError, TypeError) as exc:
        return ToolResult(
            ok=False, action=action, status="error", error=str(exc)
        ).to_json()
