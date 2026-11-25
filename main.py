import os
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages

from tool_vivado import run_vivado_simulation
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
from user_message import USER_MESSAGE
from retrieval import index_messages, retrieve_context
# --- Same setup as main.py -------------------------------------------------
model = ChatOpenAI(
    model=config["MODEL_NAME"],
    base_url=config["BASE_URL"],
    api_key=config["API_KEY"],
    timeout=1800.0,
    max_retries=0,
    use_responses_api=True
)


SYSTEM_PROMPT = ("You are an SoC debug assistant. Use tools when needed. If a simulation "
                 "fails, read its errors, reiterate. "
                 "Try a corrected action. Stop when the simulation passes and user's requirements and goals are fulfilled.")

MAX_STEPS = 1000
os.environ["AGENT_AUTO_APPROVE"] = "yes"

available_tools = {
    "get_weather": get_weather,
    "run_vivado_simulation": run_vivado_simulation,
    "query_pdf": query_pdf,
    "read_rtl": read_rtl,
    "edit_rtl": edit_rtl,
    "write_rtl": write_rtl,
    "rtl_debugger": rtl_debugger,
    "run_testbench_iverilog": run_testbench_iverilog,
    "search_files": search_files,
    "run_testbench_vivado": run_testbench_vivado,
}

model_with_tools = model.bind_tools(list(available_tools.values()))


# --- Same guardrails as main.py --------------------------------------------
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
        fn = available_tools[fn_name]
        if hasattr(fn, "invoke"):
            return fn.invoke(fn_args)
        return fn(**fn_args)
    except TypeError as exc:
        return ToolResult(ok=False, action=fn_name, status="error",
                          error=f"Invalid tool arguments: {exc}").to_json()


# --- LangGraph state -------------------------------------------------------
class State(TypedDict):
    messages: Annotated[list, add_messages]


# --- Graph nodes -----------------------------------------------------------
def agent_node(state: State):
    response_message = model_with_tools.invoke(state["messages"])
    print("[thoughts]",
          getattr(response_message, "additional_kwargs", {}).get("reasoning_content"))
    return {"messages": [response_message]}


def tools_node(state: State):
    time.sleep(5)
    last = state["messages"][-1]
    if not isinstance(last, AIMessage) or not last.tool_calls:
        return {"messages": []}
    out = []
    for tc in last.tool_calls:
        fn_name, fn_args = tc["name"], tc["args"]
        print(f"[agent] requested tool: {fn_name}({fn_args})")
        print(f"[tool] calling: {fn_name}")
        tool_result = run_tool(fn_name, fn_args)
        print(f"[tool] result: {tool_result}")
        out.append(ToolMessage(content=str(tool_result), tool_call_id=tc["id"]))
    return {"messages": out}


def index_node(state: State):
    """Embed and index new messages (human + tool outputs) into the store."""
    index_messages(state["messages"])
    return {"messages": []}


def retrieve_node(state: State):
    """Retrieve relevant chunks and append them near the end of the context."""
    messages = state["messages"]
    query = messages[-1].content if messages else ""
    hits = retrieve_context(query)
    if not hits:
        return {"messages": []}
    block = "\n\n".join(f"[{i}] {h}" for i, h in enumerate(hits, 1))
    return {"messages": [HumanMessage(
        content=f"Retrieved context from earlier in the conversation:\n{block}")]}


def route_from_agent(state: State):
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        return "tools"
    print("The agent decided it is done and no tools were called.")
    return END


# --- Build and run the graph ----------------------------------------------
builder = StateGraph(State)
builder.add_node("agent", agent_node)
builder.add_node("tools", tools_node)
builder.add_node("index", index_node)
builder.add_node("retrieve", retrieve_node)
builder.add_edge(START, "index")
builder.add_edge("index", "retrieve")
builder.add_edge("retrieve", "agent")
builder.add_conditional_edges("agent", route_from_agent, {"tools": "tools", END: END})
builder.add_edge("tools", "index")
graph = builder.compile()


def main():
    initial = {"messages": [SystemMessage(content=SYSTEM_PROMPT),
                            HumanMessage(content=USER_MESSAGE)]}
    result = graph.invoke(initial, config={"recursion_limit": MAX_STEPS})
    final = result["messages"][-1]
    print(f"\nFinal Answer:\n{final.content}")


if __name__ == "__main__":
    main()
