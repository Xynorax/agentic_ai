"""Safety layer for the SoC agent tools.

Three guardrails live here:
  1. Path allowlists (no reading/executing outside approved folders)
  2. Structured tool results (machine-readable pass/fail signals for the LLM)
  3. A risk policy that tells the agent loop which tool calls need a human OK
"""

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional

# --- 1. Allowlists ---------------------------------------------------------
# Read-only knowledge sources (PDFs)
ALLOWED_KNOWLEDGE_ROOTS = (
    r"D:\Youssef\TUM\Books\chip_design",
    r"D:\Youssef\Projects\agentic_ai",
)
# Vivado projects the agent is allowed to touch
ALLOWED_PROJECT_ROOTS = (
    r"D:\Youssef\Projects\agentic_ai_generated",
)

# --- 3. Risk policy --------------------------------------------------------
READ = "read"              # no approval needed
SIDE_EFFECT = "side_effect"  # launches a process / writes sim outputs -> approve
WRITE = "write"            # modifies source files -> approve (and back up)

TOOL_RISK = {
    "get_weather": READ,
    "query_pdf": READ,
    "read_rtl": READ,
    "run_vivado_simulation": SIDE_EFFECT,
    "run_testbench_iverilog": SIDE_EFFECT,
    "edit_rtl": WRITE,
}


class SecurityError(ValueError):
    """Raised when a tool argument violates a safety rule."""


def _normalized(path: str) -> str:
    # realpath collapses '..' and resolves symlinks/junctions; normcase makes
    # Windows path comparison case-insensitive and normalises separators.
    return os.path.normcase(os.path.realpath(os.path.abspath(os.path.expanduser(path))))


def _within(path: str, root: str) -> bool:
    p, r = _normalized(path), _normalized(root)
    return p == r or p.startswith(r + os.sep)


def validate_path(
    value: Any,
    roots,
    *,
    allowed_suffixes=None,
    must_exist: bool = True,
    label: str = "path",
) -> str:
    """Validate a user/LLM-supplied path against an allowlist.

    Returns the canonical path, or raises SecurityError / FileNotFoundError.
    """
    if not isinstance(value, str) or not value.strip():
        raise SecurityError(f"{label} must be a non-empty string.")
    if "\x00" in value:
        raise SecurityError(f"{label} contains a null byte.")

    candidate = os.path.abspath(os.path.expanduser(value))
    if not any(_within(candidate, root) for root in roots):
        raise SecurityError(
            f"{label} '{value}' is outside the allowed directories: "
            + ", ".join(roots)
        )
    if allowed_suffixes and os.path.splitext(candidate)[1].lower() not in allowed_suffixes:
        raise SecurityError(
            f"{label} must have one of these extensions: {', '.join(sorted(allowed_suffixes))}"
        )
    if must_exist:
        if not os.path.exists(candidate):
            raise FileNotFoundError(f"{label} not found: '{candidate}'")
        if not os.path.isfile(candidate):
            raise SecurityError(f"{label} must be a file, not a directory: '{candidate}'")
    return candidate


def validate_vivado_project(value: Any) -> str:
    return validate_path(
        value,
        ALLOWED_PROJECT_ROOTS,
        allowed_suffixes={".xpr"},
        label="Vivado project path",
    )


RTL_SUFFIXES = {".v", ".sv", ".svh", ".vh", ".xdc", ".sdc", ".tcl", ".f", ".do", ".txt"}


def validate_rtl_path(value: Any) -> str:
    """Only source/constraint files inside the generated project may be read or edited."""
    return validate_path(
        value,
        ALLOWED_PROJECT_ROOTS,
        allowed_suffixes=RTL_SUFFIXES,
        label="RTL file path",
    )


_SIMSET_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")


def validate_simset(value: Any) -> str:
    """Prevent Tcl injection through the simset argument."""
    if not isinstance(value, str) or not _SIMSET_RE.fullmatch(value):
        raise SecurityError(
            "simset must be a simple identifier (letters, digits, underscores; "
            "max 64 chars, starting with a letter)."
        )
    return value


def tcl_quote(value: str) -> str:
    """Safely embed a validated path inside a double-quoted Tcl string."""
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace("$", "\\$")
    )
    return '"' + escaped + '"'


# --- 2. Structured results -------------------------------------------------
@dataclass
class ToolResult:
    """A compact, machine-readable tool outcome for the agent to reason over."""

    ok: bool
    action: str
    status: str  # pass | fail | error | denied | ok
    data: dict = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "action": self.action,
            "status": self.status,
            "data": self.data,
            "error": self.error,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


_ERROR_LINE = re.compile(r"^\s*(ERROR|CRITICAL WARNING|WARNING)\s*:", re.IGNORECASE)
_FAIL_MARKERS = ("test failed", "simulation failed", "failure", "*** fail")
_PASS_MARKERS = ("test passed", "simulation passed", "*** pass")


def parse_vivado_log(lines) -> tuple:
    """Return (status, errors, warnings) from Vivado stdout lines."""
    errors, warnings, signals = [], [], []
    for raw in lines:
        line = raw.strip()
        match = _ERROR_LINE.match(line)
        if match:
            target = errors if match.group(1).upper() == "ERROR" else warnings
            target.append(line[:500])
        lower = line.lower()
        if any(marker in lower for marker in _FAIL_MARKERS):
            signals.append("fail")
        elif any(marker in lower for marker in _PASS_MARKERS):
            signals.append("pass")

    if errors or "fail" in signals:
        status = "fail"
    elif "pass" in signals:
        status = "pass"
    else:
        status = "unknown"
    return status, errors[:20], warnings[:20]


def risk_of(tool_name: str) -> str:
    """Unknown tools are treated as WRITE so they can never run unattended."""
    return TOOL_RISK.get(tool_name, WRITE)
