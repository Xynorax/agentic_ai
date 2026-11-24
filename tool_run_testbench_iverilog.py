"""Run a Verilog/SystemVerilog testbench with Icarus Verilog (iverilog + vvp)."""

import os
import re
import subprocess
import tempfile

from langchain_core.tools import tool

from safety import (
    ALLOWED_PROJECT_ROOTS,
    SecurityError,
    ToolResult,
    validate_path,
)

ACTION = "run_testbench_iverilog"

_FAIL_MARKERS = ("test failed", "*** fail", "verror", "simulation failed")
_PASS_MARKERS = ("test passed", "test pass", "*** pass")
_MAX_ERRORS = 20
_MAX_TAIL = 4000


def _errors_from(output):
    errs = []
    for line in (output or "").splitlines():
        line = line.strip()
        if line and "error" in line.lower():
            errs.append(line[:500])
    return errs[:_MAX_ERRORS]


def _tail(text):
    return "".join((text or "").splitlines(keepends=True)[-40:])[-_MAX_TAIL:]


@tool
def run_testbench_iverilog(files, top_module=None, timeout=300):
    """Compile (iverilog) then run (vvp) the testbench and RTL files in `files`.
    Does not support UVM testbenches. Do not use the tool for UVM testbenches.
    """
    action = ACTION
    try:
        if not isinstance(files, (list, tuple)) or not files:
            raise ValueError("files must be a non-empty list of paths.")
        safe_files = [
            validate_path(
                f,
                ALLOWED_PROJECT_ROOTS,
                allowed_suffixes={".v", ".sv", ".svh"},
                label="testbench file",
            )
            for f in files
        ]
        if top_module:
            if not isinstance(top_module, str) or not re.fullmatch(
                r"[A-Za-z_]\w*", top_module.strip()
            ):
                raise ValueError("top_module must be a valid Verilog/SV module name.")
            top_module = top_module.strip()
        if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
            raise ValueError("timeout must be a positive integer (seconds).")

        with tempfile.TemporaryDirectory() as tmpdir:
            out_base = os.path.join(tmpdir, "sim")
            compile_cmd = ["iverilog", "-o", out_base]
            if top_module:
                compile_cmd += ["-s", top_module]
            compile_cmd += safe_files

            try:
                proc = subprocess.run(
                    compile_cmd,
                    capture_output=True,
                    text=True,
                    errors="replace",
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired:
                return ToolResult(
                    ok=False,
                    action=action,
                    status="error",
                    error=f"iverilog timed out after {timeout} seconds.",
                ).to_json()
            compile_out = (proc.stdout or "") + (proc.stderr or "")
            if proc.returncode != 0:
                return ToolResult(
                    ok=False,
                    action=action,
                    status="error",
                    data={
                        "stage": "compile",
                        "exit_code": proc.returncode,
                        "errors": _errors_from(compile_out),
                        "output": _tail(compile_out),
                    },
                ).to_json()

            vvp_target = out_base if os.path.exists(out_base) else out_base + ".vvp"
            if not os.path.exists(vvp_target):
                return ToolResult(
                    ok=False,
                    action=action,
                    status="error",
                    error="iverilog did not produce a simulation output file.",
                ).to_json()

            try:
                run = subprocess.run(
                    ["vvp", vvp_target],
                    capture_output=True,
                    text=True,
                    errors="replace",
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired:
                return ToolResult(
                    ok=False,
                    action=action,
                    status="error",
                    error=f"simulation timed out after {timeout} seconds.",
                ).to_json()
            sim_out = (run.stdout or "") + (run.stderr or "")
            lowered = sim_out.lower()
            if run.returncode != 0 or any(m in lowered for m in _FAIL_MARKERS):
                status = "fail"
            elif any(m in lowered for m in _PASS_MARKERS):
                status = "pass"
            else:
                status = "ok"
            return ToolResult(
                ok=(status == "pass"),
                action=action,
                status=status,
                data={
                    "files": safe_files,
                    "stage": "run",
                    "exit_code": run.returncode,
                    "errors": _errors_from(sim_out),
                    "output": _tail(sim_out),
                },
            ).to_json()
    except (SecurityError, FileNotFoundError, OSError, ValueError, TypeError) as exc:
        return ToolResult(
            ok=False, action=action, status="error", error=str(exc)
        ).to_json()
