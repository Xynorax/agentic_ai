"""Minimal, safety-checked RTL reader/editor tools for the SoC agent.

Both functions return a JSON ToolResult string. The editor requires the target
text to appear exactly once, keeps a timestamped backup, and writes atomically.
"""

import os
import shutil
import tempfile
import time

from langchain_core.tools import tool

from safety import SecurityError, ToolResult, validate_rtl_path

MAX_READ_LINES = 400
MAX_EDIT_CHARS = 20000


@tool
def read_rtl(path: str, start_line: int = 1, end_line: int = None) -> str:
    """Read numbered lines of an RTL file inside the allowed project."""
    action = "read_rtl"
    try:
        safe_path = validate_rtl_path(path)
        with open(safe_path, "r", encoding="utf-8", newline="") as handle:
            lines = handle.readlines()

        start = int(start_line)
        end = int(end_line) if end_line is not None else len(lines)
        if start < 1 or end < start:
            raise ValueError("start_line must be >= 1 and end_line >= start_line.")
        if end - start + 1 > MAX_READ_LINES:
            raise ValueError(f"Read at most {MAX_READ_LINES} lines per call.")

        chosen = lines[start - 1 : end]
        body = "\n".join(
            f"{start + i}: {line.rstrip(chr(10)).rstrip(chr(13))}"
            for i, line in enumerate(chosen)
        )
        return ToolResult(
            ok=True,
            action=action,
            status="ok",
            data={
                "path": safe_path,
                "total_lines": len(lines),
                "start_line": start,
                "end_line": min(end, len(lines)),
                "content": body,
            },
        ).to_json()
    except (SecurityError, FileNotFoundError, OSError, ValueError, TypeError) as exc:
        return ToolResult(ok=False, action=action, status="error", error=str(exc)).to_json()


@tool
def edit_rtl(path: str, old: str, new: str) -> str:
    """Replace exactly one occurrence of `old` with `new`, keeping a backup."""
    action = "edit_rtl"
    try:
        safe_path = validate_rtl_path(path)
        if not isinstance(old, str) or not old:
            raise ValueError("'old' must be a non-empty string.")
        if not isinstance(new, str):
            raise ValueError("'new' must be a string.")
        if old == new:
            raise ValueError("'old' and 'new' are identical; nothing to change.")
        if len(old) > MAX_EDIT_CHARS or len(new) > MAX_EDIT_CHARS:
            raise ValueError(f"'old' and 'new' must be under {MAX_EDIT_CHARS} characters.")

        with open(safe_path, "r", encoding="utf-8", newline="") as handle:
            original = handle.read()

        matches = original.count(old)
        if matches != 1:
            raise ValueError(
                f"'old' must match exactly once in the file; found {matches} match(es). "
                "Include more surrounding context to disambiguate."
            )

        updated = original.replace(old, new, 1)
        backup_path = f"{safe_path}.{time.strftime('%Y%m%d-%H%M%S')}.bak"
        shutil.copy2(safe_path, backup_path)

        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", newline="",
                dir=os.path.dirname(safe_path), delete=False
            ) as handle:
                handle.write(updated)
                temp_path = handle.name
            os.replace(temp_path, safe_path)  # atomic on Windows
            temp_path = None
        finally:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)

        return ToolResult(
            ok=True,
            action=action,
            status="ok",
            data={
                "path": safe_path,
                "backup": backup_path,
                "old_chars": len(old),
                "new_chars": len(new),
                "lines_before": len(original.splitlines()),
                "lines_after": len(updated.splitlines()),
            },
        ).to_json()
    except (SecurityError, FileNotFoundError, OSError, ValueError, TypeError) as exc:
        return ToolResult(ok=False, action=action, status="error", error=str(exc)).to_json()
