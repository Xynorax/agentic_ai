from tool_vivado import run_vivado_simulation
from openai import OpenAI # Point client to your local Ollama server
import json
import os
from tools_Schemas import tools
from tool_weather import get_weather
from tool_pdf_parser import query_pdf
from tool_rtl_editor import read_rtl, edit_rtl
from tool_rtl_debugger import rtl_debugger
from tool_rtl_writer import write_rtl
from tool_run_testbench_iverilog import run_testbench_iverilog
from tool_run_testbench_vivado import run_testbench_vivado
from tool_search import search_files
from safety import READ, ToolResult, risk_of
from Config import config
import time

AI_MODEL = "gemini-3.1-flash-lite"
#Initialize AI client
client = OpenAI(
    #base_url="http://localhost:11434/v1", # Local ollama server
    base_url = config["BASE_URL"],
    api_key=config["API_KEY"],
    timeout=1800.0,   # 30 minutes
    max_retries=0,    # don't silently repeat a 30-minute call twice
)

#USER_MESSAGE = "Run the simulation of this project D:\\Youssef\\Projects\\agentic_ai_generated\\picorv32\\picorv32_vivado\\picorv32_vivado.xpr and fix any errors that arise. You can use read_rtl/edit_rtl to read or edit RTL files. You can use debug tool. Root directory is D:\\Youssef\\Projects\\agentic_ai_generated\\picorv32; at this path you can find the top level testbench.sv"
USER_MESSAGE = ("Write a counter in verilog that counts to 4 then wraps around. Write a UVM testbench for it. Read UVM book to understand how and use web. Write the verilog and SV files in D:\\Youssef\\Projects\\agentic_ai_generated\\agentic_ai_chip")
MAX_STEPS = 1000
os.environ["AGENT_AUTO_APPROVE"] = "yes"

# Tools definitions
## 1. Map tool names to python functions
available_tools = {
    "get_weather": get_weather, # Test tool
    "run_vivado_simulation": run_vivado_simulation,
    "query_pdf": query_pdf,
    "read_rtl": read_rtl,
    "edit_rtl": edit_rtl,
    "write_rtl": write_rtl,
    "rtl_debugger": rtl_debugger,
    "run_testbench_iverilog": run_testbench_iverilog,
    "search_files": search_files,
    "run_testbench_vivado": run_testbench_vivado
}

# Guardrail: Check if the agent wants to call a tool
def _has_tool_intent(msg) -> bool:
    """Ask the model itself whether the previous message shows tool-calling intent."""
    text = " ".join(filter(None, [
        getattr(msg, "content", None) or "",
        getattr(msg, "reasoning_content", None) or "",
        getattr(msg, "reasoning", None) or "",
    ])).strip()
    if not text:
        return False
    ask = client.chat.completions.create(
        model=config["MODEL_NAME"],
        messages=[{
            "role": "user",
            "content": (
                "This message did not include any tool calls:\n\n"
                f"{text}\n\n"
                "Did you intend to call a tool (e.g. write_rtl, edit_rtl, "
                "run_testbench_iverilog) but fail to issue the tool call? "
                "Answer with YES or NO only."
            ),
        }],
    )
    answer = (ask.choices[0].message.content or "").strip().upper()
    answer_thoughts = ask.choices[0].message
    print("[thoughts]",
          getattr(answer_thoughts, "reasoning_content", None) or getattr(answer_thoughts, "reasoning", None))
    return answer.startswith("YES")
# --- Guardrail 3: human approval for side-effecting / write tools -----------
def request_approval(tool_name: str, fn_args: dict) -> bool:
    if os.getenv("AGENT_AUTO_APPROVE", "").strip().lower() in {"1", "true", "yes"}:
        return True
    print(f"\n[!] Approval required for {risk_of(tool_name)} action: {tool_name}({fn_args})")
    try:
        answer = input("Allow this action? [y/N]: ").strip().lower()
    except EOFError:
        return False
    return answer in {"y", "yes"}


def run_tool(fn_name: str, fn_args: dict) -> str:
    """Execute one tool behind the guardrails; always return a string for the LLM."""
    if fn_name not in available_tools:
        return ToolResult(ok=False, action=fn_name, status="error",
                          error=f"Unknown tool '{fn_name}'.").to_json()
    if risk_of(fn_name) != READ and not request_approval(fn_name, fn_args):
        return ToolResult(ok=False, action=fn_name, status="denied",
                          error="User denied this action.").to_json()
    try:
        return available_tools[fn_name](**fn_args)
    except TypeError as exc:
        return ToolResult(ok=False, action=fn_name, status="error",
                          error=f"Invalid tool arguments: {exc}").to_json()


def loop_message(fn_name: str, tool_result: str):
    """Close the loop: turn a FAILED simulation result into the next instruction."""
    if fn_name not in ("run_vivado_simulation", "run_testbench_iverilog"):
        return None
    try:
        outcome = json.loads(tool_result)
    except (TypeError, ValueError):
        return None
    if outcome.get("status") != "fail":
        return None

    errors = outcome.get("data", {}).get("errors", [])
    return (
        f"The {fn_name} run FAILED. Errors: "
        + ("; ".join(errors[:3]) or "see the tool result")
        + ". Research the error with query_pdf if useful, propose a concrete fix, "
          f"then call {fn_name} again with the corrected setup."
    )


def main():
    messages = [
        {"role": "system", "content": (
            "You are an SoC debug assistant. Use tools when needed. If a simulation "
            "fails, read its errors, and call the debugger"
            "try a corrected action. Stop when the simulation passes."
        )},
        {"role": "user", "content": USER_MESSAGE}
    ]

    for step in range(1, MAX_STEPS + 1):
        print(f"\n--- step {step}/{MAX_STEPS} ---")
        response = client.chat.completions.create(
            model=config["MODEL_NAME"],
            messages=messages,
            tools=tools,
        )
        time.sleep(3)
        response_message = response.choices[0].message
        print("[thoughts]",
              getattr(response_message, "reasoning_content", None) or getattr(response_message, "reasoning", None))
        messages.append(response_message)

        # The agent decided it is done (or is ready to explain the result).
        if not response_message.tool_calls:
            if _has_tool_intent(response_message):
                print("_has_tool_intent called")
                messages.append({
                    "role": "user",
                    "content": (
                        "You indicated you need to call a tool, but no tool call "
                        "was issued. Issue the tool invocation now as a "
                        "structured tool_call and continue; put the call in the "
                        "tool_calls field, not in text."
                    ),
                })
                print("Continuing")
                continue
            print("The agent decided it is done and no tools were called.")
            print(f"\nFinal Answer:\n{response_message.content}")
            return response_message.content

        sim_passed = False
        for tool_call in response_message.tool_calls:
            fn_name = tool_call.function.name
            fn_args = json.loads(tool_call.function.arguments)
            print(f"[agent] requested tool: {fn_name}({fn_args})")

            tool_result = run_tool(fn_name, fn_args)
            print(f"[tool] result: {tool_result}")
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": str(tool_result)
            })

            # --- Close the loop: act on the simulation output -------------
            nudge = loop_message(fn_name, tool_result)
            if nudge:
                print("[loop] simulation failed -> feeding errors back to agent")
                messages.append({"role": "user", "content": nudge})
            elif fn_name in ("run_vivado_simulation", "run_testbench_iverilog"):
                try:
                    sim_passed = json.loads(tool_result).get("status") == "pass"
                except (TypeError, ValueError):
                    pass

        if sim_passed:
            final = client.chat.completions.create(
                model= config["MODEL_NAME"],
                messages=messages
            )
            print(f"\nFinal Answer:\n{final.choices[0].message.content}")
            return final.choices[0].message.content

    print("\nStopped: reached step limit without a passing simulation.")
    return None


if __name__ == "__main__":
    main()
