"""Deterministic debugging aid for simulation failures (READ-only).

When the agent needs to explain why a behavioral simulation fails, this tool
reads the RTL sources around the reported errors and emits structured facts:

* parsed error locations and short source snippets,
* the instantiation port map of the top module inside the testbench,
* constants connected to ports (e.g. ``.resetn(0)``),
* module ports that are never connected,
* testbench signals that are declared but never referenced.

It cross-checks the instantiation against every definition of the module so
typos like ``.clk`` on a module that only has ``sys_clk`` are flagged.

Everything it returns is derived from files inside the allowed RTL project
root; the tool only reads and never writes, and needs no human approval.
"""

import fnmatch
import os
import re
from typing import Any, Optional

from safety import (
    ALLOWED_KNOWLEDGE_ROOTS,
    ALLOWED_PROJECT_ROOTS,
    SecurityError,
    ToolResult,
    validate_rtl_path,
)

ACTION = "diagnose_simulation_failure"

_RTL_SUFFIXES = {".v", ".sv", ".svh", ".vh", ".xdc", ".sdc", ".tcl", ".f", ".do", ".txt"}
_MAX_ERRORS = 10
_MAX_SNIPPETS = 8
_MAX_INSTANTIATIONS = 5
_MAX_FILES = 2000
_MAX_HINTS = 12
_MAX_FILE_BYTES = 1_000_000

# e.g. "...[/D:/Youssef/Projects/.../testbench.sv:72]" or "file.sv:72"
_ERROR_PATH_RE = re.compile(
    r"((?:[A-Za-z]:)?[A-Za-z0-9._/\\-]+\.(?:v|sv|svh|vh|xdc|sdc|tcl|f|do|txt)):(\d+)"
)
_DECL_RE = re.compile(
    r"\b(?:reg|wire|integer|real|logic|bit|byte)\s+(?:\[[^\]]*\]\s+)*([A-Za-z_]\w*)"
)
_INST_RE = re.compile(
    r"\b(?P<mod>[A-Za-z_]\w*)\s*(?:#\s*\([^;]*?\))?\s*"
    r"(?P<inst>[A-Za-z_]\w*)\s*\((?P<ports>.*?)\)\s*;",
    re.DOTALL,
)
_CONN_RE = re.compile(r"\.(?P<port>[A-Za-z_]\w*)\s*\(\s*(?P<expr>[^()]*?)\s*\)")
_MODULE_RE = re.compile(r"\bmodule\s+[A-Za-z_]\w*\b")
_KEYWORDS = {
    "input", "output", "inout", "wire", "reg", "logic", "bit", "byte", "integer",
    "real", "tri", "wor", "wand", "signed", "unsigned", "localparam", "parameter",
    "assign", "module", "endmodule", "always", "if", "else", "begin", "end",
    "for", "while", "default", "case", "endcase",
}
_CONST_RE = re.compile(
    r"^(?:\d+'[bB][01]+|\d+'[hH][0-9a-fA-F]+|\d+'[dD][0-9]+|\d+'[oO][0-7]+|"
    r"'[bB][01]+|'[hH][0-9a-fA-F]+|'[dD][0-9]+|'[oO][0-7]+|\d+|'0|'1)$"
)


def _is_within_any(path: str, roots) -> bool:
    p = os.path.normcase(os.path.realpath(os.path.abspath(os.path.expanduser(path))))
    for r in roots:
        rn = os.path.normcase(os.path.realpath(os.path.abspath(os.path.expanduser(r))))
        if p == rn or p.startswith(rn + os.sep):
            return True
    return False


_ALLOWED_ROOTS = tuple(ALLOWED_PROJECT_ROOTS) + tuple(ALLOWED_KNOWLEDGE_ROOTS)


def _safe_read(path: str) -> Optional[str]:
    try:
        cand = os.path.abspath(os.path.expanduser(path))
        if not _is_within_any(cand, _ALLOWED_ROOTS):
            return None
        with open(cand, "r", encoding="utf-8", errors="replace", newline="") as fh:
            return fh.read()
    except OSError:
        return None


def _match_paren(text: str, open_idx: int):
    """Return (inner_text, close_idx) for the paren at open_idx, else (None, -1)."""
    depth = 0
    for i in range(open_idx, len(text)):
        c = text[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return text[open_idx + 1:i], i
    return None, -1


def _walk_sources(root: str):
    out = []
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for fn in filenames:
                if os.path.splitext(fn)[1].lower() not in _RTL_SUFFIXES:
                    continue
                p = os.path.join(dirpath, fn)
                src = _safe_read(p)
                if src is not None and len(src) <= _MAX_FILE_BYTES:
                    out.append((p, src))
            if len(out) >= _MAX_FILES:
                break
    except OSError:
        pass
    return out


def _module_defs(sources, module_name: str):
    """All (file, src, port_names) for definitions of module_name."""
    defs = []
    for path, src in sources:
        if not re.search(r"\bmodule\s+" + re.escape(module_name) + r"\b", src):
            continue
        m = re.search(r"\bmodule\s+" + re.escape(module_name) + r"\b", src)
        groups = []
        pos = m.end()
        while True:
            idx = src.find("(", pos)
            if idx == -1:
                break
            inner, end = _match_paren(src, idx)
            if inner is None:
                break
            nxt = src[end + 1:end + 2]
            groups.append(inner)
            pos = end + 1
            if nxt == ";":
                break
        port_text = groups[-1] if groups else ""
        names = []
        for tok in re.findall(r"\b[A-Za-z_]\w*\b", port_text):
            if tok not in _KEYWORDS and tok not in names:
                names.append(tok)
        defs.append({"file": path, "src": src, "ports": names})
    return defs


def _union_ports(defs) -> list:
    seen = []
    for d in defs:
        for p in d["ports"]:
            if p not in seen:
                seen.append(p)
    return seen


def _extract_instantiations(src: str, module_name: str, default_file: str):
    out = []
    for m in _INST_RE.finditer(src):
        if m.group("mod") != module_name:
            continue
        port_map = {}
        conn_lines = {}
        for cm in _CONN_RE.finditer(m.group("ports")):
            port_map[cm.group("port")] = cm.group("expr").strip()
            conn_lines[cm.group("port")] = (
                src[: m.start("ports") + cm.start()].count("\n") + 1
            )
        if not port_map and not m.group("ports").strip():
            continue
        line = src[:m.start()].count("\n") + 1
        out.append({
            "instance": m.group("inst"),
            "file": default_file,
            "line": line,
            "port_map": port_map,
            "conn_lines": conn_lines,
        })
    return out[:_MAX_INSTANTIATIONS]


def _is_constant(expr: str) -> bool:
    e = expr.strip().strip("{}").strip().strip("'").strip()
    return bool(_CONST_RE.match(e))


def _parse_errors(errors) -> list:
    entries = []
    if errors is None:
        return entries
    raw_lines = []
    if isinstance(errors, str):
        raw_lines = [l for l in errors.splitlines() if l.strip()]
    elif isinstance(errors, (list, tuple)):
        for e in errors[:_MAX_ERRORS]:
            if isinstance(e, dict):
                msg = e.get("message") or e.get("error") or json_dumps(e)
                if e.get("file") and e.get("line"):
                    entries.append({
                        "file": str(e["file"]).replace("/", os.sep),
                        "line": int(e["line"]),
                        "raw": str(msg)[:600].strip(),
                    })
                    continue
                raw_lines.append(str(msg)[:600])
            else:
                raw_lines.append(str(e)[:600])
    else:
        raw_lines.append(str(errors)[:600])

    for raw in raw_lines[:_MAX_ERRORS]:
        text = raw.strip()
        entry = {"file": None, "line": None, "raw": text}
        m = _ERROR_PATH_RE.search(text)
        if m:
            entry["file"] = m.group(1).replace("/", os.sep)
            entry["line"] = int(m.group(2))
        entries.append(entry)
    return entries


def json_dumps(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False, default=str)


def _snippet(src: str, line_no: int, width: int = 5):
    lines = src.splitlines()
    if not lines:
        return []
    start = max(1, line_no - width)
    end = min(len(lines), line_no + width)
    return [{"line": i, "text": lines[i - 1].strip()[:160]} for i in range(start, end + 1)]


def _snippets(entries):
    out = []
    seen = set()
    for e in entries:
        if not e.get("file") or not e.get("line"):
            continue
        key = (e["file"], e["line"])
        if key in seen:
            continue
        seen.add(key)
        src = _safe_read(e["file"])
        if src:
            out.append({"file": e["file"], "line": e["line"],
                        "lines": _snippet(src, e["line"])})
    return out[:_MAX_SNIPPETS]


def _unused_signals(src: str):
    counts = {}
    for m in _DECL_RE.finditer(src):
        n = m.group(1)
        if n not in counts:
            counts[n] = src.count(n)
    return sorted(n for n, c in counts.items() if c <= 1)


def rtl_debugger(
    testbench_path: str,
    module_name: str,
    errors=None,
    project_root=None,
) -> str:
    """Inspect RTL around a simulation failure; return a JSON ToolResult."""
    action = ACTION
    try:
        safe_tb = validate_rtl_path(testbench_path)
        root = (
            os.path.abspath(os.path.expanduser(project_root))
            if project_root
            else os.path.dirname(safe_tb)
        )
        if not os.path.isdir(root) or not _is_within_any(root, _ALLOWED_ROOTS):
            raise SecurityError(
                f"project_root '{project_root}' must be inside the allowed RTL directories."
            )

        tb_src = _safe_read(safe_tb)
        sources = _walk_sources(root)
        module_defs = _module_defs(sources, module_name)
        module_ports = _union_ports(module_defs)
        module_files = [d["file"] for d in module_defs]

        instantiations = (
            _extract_instantiations(tb_src, module_name, safe_tb) if tb_src else []
        )
        connected = set()
        for inst in instantiations:
            connected.update(inst["port_map"].keys())
        unconnected = [p for p in module_ports if p not in connected]

        constants_on_ports = []
        for inst in instantiations:
            conn_lines = inst.get("conn_lines", {})
            for port, expr in inst["port_map"].items():
                if _is_constant(expr):
                    entry = {
                        "port": port,
                        "expr": expr,
                        "instance": inst["instance"],
                        "file": inst["file"],
                        "line": conn_lines.get(port, inst["line"]),
                    }
                    if re.search(r"reset|rst", port, re.I) or port in ("clk", "clock"):
                        entry["note"] = "reset/clock-like port driven by a constant"
                    constants_on_ports.append(entry)

        unused_signals = _unused_signals(tb_src) if tb_src else []

        entries = _parse_errors(errors)
        snippets = _snippets(entries)

        hints = []
        if not instantiations and tb_src:
            hints.append(
                f"No instantiation of module '{module_name}' was found in {safe_tb}. "
                f"Use find_rtl_files('{module_name}*') to locate its definition file, "
                f"then read it with read_rtl."
            )
        for c in constants_on_ports:
            hint = (
                f"Module '{module_name}' port '{c['port']}' is connected to the constant "
                f"'{c['expr']}' at {c['file']}:{c['line']} (instance '{c['instance']}'). "
                "Find the signal this port should be driven by and connect it instead."
            )
            if "note" in c:
                hint += " This looks like a reset/clock port -- verify reset polarity and signal naming."
            hints.append(hint)
        for inst in instantiations:
            for conn in inst["port_map"]:
                if module_ports and conn not in module_ports:
                    hints.append(
                        f"Connection '{conn}' is not a port of module '{module_name}' "
                        f"(defined ports: {module_ports}). Check for typos -- maybe a similar "
                        "name in the module definition is intended."
                    )
            if module_ports:
                for p in unconnected:
                    hints.append(
                        f"Module port '{p}' of '{module_name}' is not connected at "
                        f"{inst['file']}:{inst['line']} -- drive it from the testbench or remove it."
                    )
        for sig in unused_signals:
            hints.append(
                f"Testbench signal '{sig}' is declared but never referenced -- check for "
                "injected bugs or leftover code."
            )

        data = {
            "module_name": module_name,
            "module_files": module_files,
            "module_ports": module_ports,
            "testbench_path": safe_tb,
            "error_locations": [
                {"file": e["file"], "line": e["line"], "raw": e["raw"]}
                for e in entries if e.get("file")
            ],
            "snippets": snippets,
            "instantiations": [
                {"instance": i["instance"], "file": i["file"], "line": i["line"],
                 "port_map": i["port_map"], "conn_lines": i["conn_lines"]}
                for i in instantiations
            ],
            "constants_on_ports": constants_on_ports,
            "unconnected_module_ports": unconnected,
            "unused_testbench_signals": unused_signals,
            "hints": hints[:_MAX_HINTS],
        }
        return ToolResult(ok=True, action=action, status="ok", data=data).to_json()
    except (SecurityError, FileNotFoundError, OSError, ValueError, TypeError) as exc:
        return ToolResult(
            ok=False, action=action, status="error",
            error=f"{type(exc).__name__}: {exc}",
        ).to_json()


def find_rtl_files(pattern: str, project_root: str = None, limit: int = 100) -> str:
    """Glob-search for RTL files under the project root (READ).

    ``pattern`` is a glob matched against the full path and the file name,
    e.g. 'picorv32*', '*wrapper*', '*top*.sv'. Returns a JSON ToolResult whose
    data lists up to ``limit`` absolute paths with their sizes.
    """
    action = "find_rtl_files"
    try:
        if not isinstance(pattern, str) or not pattern.strip():
            raise ValueError("'pattern' must be a non-empty glob string.")
        pat = pattern.strip().replace("/", os.sep)
        root = (
            os.path.abspath(os.path.expanduser(project_root))
            if project_root
            else ALLOWED_PROJECT_ROOTS[0]
        )
        if not os.path.isdir(root) or not _is_within_any(root, _ALLOWED_ROOTS):
            raise SecurityError(
                f"project_root must be inside the allowed RTL directories: {root}"
            )
        matches = []
        for p, _src in _walk_sources(root):
            base = os.path.basename(p)
            if not (fnmatch.fnmatch(p, pat) or fnmatch.fnmatch(base, pat)):
                continue
            try:
                size = os.path.getsize(p)
            except OSError:
                size = 0
            matches.append({"path": p, "bytes": size})
            if len(matches) >= limit:
                break
        data = {
            "pattern": pattern,
            "root": root,
            "count": len(matches),
            "files": matches[:limit],
        }
        return ToolResult(
            ok=True, action=action,
            status="ok" if matches else "no_match",
            data=data,
        ).to_json()
    except (SecurityError, FileNotFoundError, OSError, ValueError, TypeError) as exc:
        return ToolResult(
            ok=False, action=action, status="error",
            error=f"{type(exc).__name__}: {exc}",
        ).to_json()
