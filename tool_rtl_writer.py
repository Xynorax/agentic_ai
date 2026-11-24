"""Minimal, safety-checked RTL writer tool for the SoC agent.

Writes a NEW RTL file inside the allowed project root. Refuses to
overwrite an existing file; use edit_rtl to modify existing files.
Returns a JSON ToolResult string.
"""

import os
import tempfile

from langchain_core.tools import tool

from safety import (
    SecurityError,
    ToolResult,
    validate_path,
    ALLOWED_PROJECT_ROOTS,
    RTL_SUFFIXES,
)


@tool
def write_rtl(path: str, content: str) -> str:
    """Write `content` to a new RTL file at `path` inside the allowed project."""
    action = "write_rtl"
    try:
        safe_path = validate_path(
            path,
            ALLOWED_PROJECT_ROOTS,
            allowed_suffixes=RTL_SUFFIXES,
            must_exist=False,
            label="RTL file path",
        )
        if not isinstance(content, str):
            raise ValueError("'content' must be a string.")
        if os.path.exists(safe_path):
            raise FileExistsError(
                f"RTL file already exists: '{safe_path}'. Use edit_rtl to change it."
            )
        parent = os.path.dirname(safe_path)
        if not os.path.isdir(parent):
            raise FileNotFoundError(f"Parent directory not found: '{parent}'")

        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", newline="",
                dir=parent, delete=False
            ) as handle:
                handle.write(content)
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
                "bytes_written": len(content.encode("utf-8")),
                "lines_written": len(content.splitlines()),
            },
        ).to_json()
    except (SecurityError, FileNotFoundError, OSError, ValueError, TypeError) as exc:
        return ToolResult(ok=False, action=action, status="error", error=str(exc)).to_json()
