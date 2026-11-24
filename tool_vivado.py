import os
import subprocess
import tempfile

from langchain_core.tools import tool

from safety import (
    SecurityError,
    ToolResult,
    parse_vivado_log,
    tcl_quote,
    validate_simset,
    validate_vivado_project,
)

VIVADO_PATH = r"D:\Xilinx\Vivado\2024.1\bin\vivado.bat"
SIM_TIMEOUT_SEC = 300


@tool
def run_vivado_simulation(project_path: str, simset: str = "sim_1") -> str:
    """Open a Vivado project in batch mode and run behavioral simulation.

    Returns a JSON tool result with an explicit pass/fail status and parsed
    errors/warnings, so the agent can react to a failed simulation.
    """
    action = "run_vivado_simulation"
    # Kill any vivado processes that may be running from a previous simulation.
    subprocess.run(["taskkill", "/F", "/IM", "vivado.exe", "/IM", "xsim"], capture_output=True)
    try:
        # --- Guardrail 1: validate every LLM-supplied argument first ---
        safe_project = validate_vivado_project(project_path)
        safe_simset = validate_simset(simset)
        tcl_project = tcl_quote(safe_project.replace("\\", "/"))

        tcl_commands = f"""
        open_project {tcl_project}
        launch_simulation -simset {safe_simset} -mode behavioral
        close_project
        """

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".tcl", delete=False, encoding="utf-8"
        ) as temp_tcl:
            temp_tcl.write(tcl_commands)
            temp_tcl_path = temp_tcl.name

        try:
            # --- Run Vivado with a hard timeout; communicate() drains stdout ---
            print(f"[vivado] Launching simulation for: {safe_project}")
            process = subprocess.Popen(
                [
                    VIVADO_PATH,
                    "-mode", "batch",
                    "-source", temp_tcl_path,
                    "-nolog",
                    "-nojournal",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
            )
            output, _ = process.communicate(timeout=SIM_TIMEOUT_SEC)

            status, errors, warnings = parse_vivado_log(output.splitlines())
            tail = "".join(output.splitlines(keepends=True)[-40:])[-4000:]
            result = ToolResult(
                ok=(process.returncode == 0 and status != "fail"),
                action=action,
                status=status if process.returncode == 0 else "error",
                data={
                    "project_path": safe_project,
                    "simset": safe_simset,
                    "exit_code": process.returncode,
                    "errors": errors,
                    "warnings": warnings,
                    "log_tail": tail,
                },
            )
            print(f"[vivado] status={result.status} errors={len(errors)}")
            return result.to_json()

        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            return ToolResult(
                ok=False,
                action=action,
                status="error",
                error=f"Vivado simulation timed out after {SIM_TIMEOUT_SEC} seconds.",
            ).to_json()
        finally:
            if os.path.exists(temp_tcl_path):
                os.remove(temp_tcl_path)

    except (SecurityError, FileNotFoundError, OSError, ValueError) as exc:
        return ToolResult(
            ok=False,
            action=action,
            status="error",
            error=str(exc),
        ).to_json()
