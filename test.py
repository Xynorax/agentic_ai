from Scripts.tool_vivado import run_vivado_simulation
from openai import OpenAI # Point client to your local Ollama server
import json
import os
from tools_Schemas import tools
from tool_weather import get_weather
from tool_pdf_parser import query_pdf
from tool_rtl_editor import read_rtl, edit_rtl
from tool_rtl_debugger import rtl_debugger
from tool_rtl_writer import write_rtl
from tool_run_testbench import run_testbench
from tool_search import search_files
from safety import READ, ToolResult, risk_of
import time

AI_MODEL = "gemini-3.1-flash-lite"
#Initialize AI client
client = OpenAI(
    #base_url="http://localhost:11434/v1", # Local ollama server
    base_url = "https://generativelanguage.googleapis.com/v1beta/openai/",
    api_key="AQ.Ab8RN6JzqLLTaeCp6C3UwXrVYplZWyu1cjADrlMxsFpHfoi2hw",
    timeout=1800.0,   # 30 minutes
    max_retries=0,    # don't silently repeat a 30-minute call twice
)
messages = [
        {"role": "system", "content": (
            "You are an SoC debug assistant. Use tools when needed. If a simulation "
            "fails, read its errors, and call the debugger"
            "try a corrected action. Stop when the simulation passes."
        )},
        {"role": "user", "content": "Hey"}
    ]

response = client.chat.completions.create(
            model=AI_MODEL,
            messages=messages,
            tools=tools,
        )
time.sleep(2)
response_message = response.choices[0].message
print("[thoughts]",
      getattr(response_message, "reasoning_content", None) or getattr(response_message, "reasoning", None))
