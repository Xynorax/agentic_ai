"""Run a Verilog/SystemVerilog testbench with Vivado (batch mode). Supports UVM testbenches"""

import os
import re
import subprocess
import tempfile

from langchain_core.tools import tool

from safety import (
    ALLOWED_PROJECT_ROOTS,
    SecurityError,
    ToolResult,
    parse_vivado_log,
    tcl_quote,
    validate_path,
)

ACTION = "run_testbench_vivado"
VIVADO_PATH = r"D:\Xilinx\Vivado\2024.1\bin\vivado.bat"

_MODULE_RE = re.compile(r"^\s*module\s+([A-Za-z_]\w*)", re.MULTILINE)
_TB_NAME_RE = re.compile(r"tb|testbench|test_top", re.IGNORECASE)


def _tail(text):
    return "".join((text or "").splitlines(keepends=True)[-40:])[-4000:]


def _default_top_module(safe_files):
    """Pick a top module: prefer a module whose name looks like a testbench."""
    first_module = None
    for path in safe_files:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                names = _MODULE_RE.findall(fh.read())
        except OSError:
            continue
        for name in names:
            if _TB_NAME_RE.search(name):
                return name
            if first_module is None:
                first_module = name
    return first_module


def _build_tcl(safe_files, top_module, proj_dir):
    added = "add_files -norecurse [list " + " ".join(
        tcl_quote(f) for f in safe_files
    ) + "]"
    return "\n".join([
        f"create_project sim_run {tcl_quote(proj_dir)}",
        added,
        'if {[string equal [get_filesets -quiet sim_1] ""]} {',
        "    create_fileset -simset sim_1",
        "}",
        "update_compile_order -fileset sim_1",
        f"set_property top {tcl_quote(top_module)} [get_filesets sim_1]",
        "set_property -name xsim.simulate.runtime -value all -objects [get_filesets sim_1]",
        "launch_simulation -simset sim_1 -mode behavioral",
    ]) + "\n"


@tool
def run_testbench_vivado(files, top_module=None, timeout=300):
    """Compile/run the testbench and RTL files using Vivado in batch mode.
    Supports UVM testbenches

    Generates a minimal TCL script (new temp project, add sources, set the
    top module, launch the behavioral simulation) and runs it with
    ``vivado -mode batch``. Returns a ToolResult JSON with pass/fail/error,
    exit code, errors/warnings and a log tail.

    If `top_module` is not given, it is guessed from the files: the first
    module whose name looks like a testbench is preferred, otherwise the
    first module declared in the files.
    """
    temp_tcl_path = None
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
        if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
            raise ValueError("timeout must be a positive integer (seconds).")
        if top_module is not None:
            if not isinstance(top_module, str) or not re.fullmatch(
                r"[A-Za-z_]\w*", top_module.strip()
            ):
                raise ValueError("top_module must be a valid Verilog/SV module name.")
            top_module = top_module.strip()
        else:
            top_module = _default_top_module(safe_files)
            if not top_module:
                raise ValueError(
                    "could not determine a top module from the files; "
                    "pass top_module explicitly."
                )

        proj_dir = tempfile.mkdtemp(prefix="vivado_tb_")
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".tcl", delete=False, encoding="utf-8"
        ) as temp_tcl:
            temp_tcl.write(_build_tcl(safe_files, top_module, proj_dir))
            temp_tcl_path = temp_tcl.name

        proc = subprocess.run(
            [
                VIVADO_PATH,
                "-mode", "batch",
                "-source", temp_tcl_path,
                "-nolog",
                "-nojournal",
            ],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return ToolResult(
            ok=False,
            action=ACTION,
            status="error",
            error=f"Vivado run timed out after {timeout} seconds.",
        ).to_json()
    except (SecurityError, FileNotFoundError, OSError, ValueError, TypeError) as exc:
        return ToolResult(
            ok=False, action=ACTION, status="error", error=str(exc)
        ).to_json()
    finally:
        if temp_tcl_path and os.path.exists(temp_tcl_path):
            os.remove(temp_tcl_path)

    output = (proc.stdout or "") + (proc.stderr or "")
    status, errors, warnings = parse_vivado_log(output.splitlines())
    if proc.returncode != 0:
        status = "error"
    elif status != "fail":
        status = "pass"

    return ToolResult(
        ok=status == "pass",
        action=ACTION,
        status=status,
        data={
            "files": safe_files,
            "exit_code": proc.returncode,
            "errors": errors,
            "warnings": warnings,
            "log_tail": _tail(output),
        },
    ).to_json()
