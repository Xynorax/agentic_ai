## Write the tool schema; required to make it easier for LLMs to understand what the tool expects, does and returns
tools = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get current weather conditions for a given city.",
            "parameters": {
                "type": "object",
                "properties":{
                    "location": {
                        "type": "string",
                        "description": "The city name, e.g. Tokyo or Paris"
                    }
                },
                "required": ["location"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_vivado_simulation",
            "description": (
                "Opens a Xilinx Vivado project (.xpr) inside "
                "the specified path and runs a behavioral simulation. "
                "Returns JSON with status "
                "(pass/fail/error), parsed errors and warnings, and a log tail."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_path": {
                        "type": "string",
                        "description": (
                            "Full Windows path to a .xpr project file "
                        )
                    },
                    "simset": {
                        "type": "string",
                        "description": (
                            "Simulation set name: letters, digits and underscores only "
                            "(e.g. 'sim_1'). Defaults to 'sim_1'."
                        )
                    }
                },
                "required": ["project_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "query_pdf",
            "description": (
                "Search a PDF book about chip/VLSI design for passages relevant to a "
                "question, using local semantic search. The PDFs live in "
                "D:/Youssef/TUM/Books/chip_design and include: uvm primer.pdf, "
                "HandP_RISCV.pdf, Springer - VLSI Physical Design.pdf, "
                "Integrated Circuit Design IC Design Flow.pdf, "
                "Chips and Intelligence Low Power VLSI Design with Artificial Intelligence.pdf, "
                "Essentials of Electronic Testing for Digital Memory & Mixed Signal VLSI Circuits.pdf, "
                "Introduction to Logic Circuits & Logic Design with VHDL.pdf, "
                "978-0-387-46115-1.pdf, 978-3-031-58075-8.pdf, ourdev_535872.pdf, "
                "pdfcoffee.com_a-practical-guide-to-adopting-the-universal-verification-methodology-uvm-ebook-sharon-rosenberg-kathleen-meade-pdf-free.pdf, "
                "[Mostafa_Abd-El-Barr__Hesham_El-Rewini]_Fundamenta(BookZZ.org).pdf. "
                "Only PDFs inside D:/Youssef/TUM/Books/chip_design or "
                "D:/Youssef/Projects/agentic_ai are allowed; a full Windows path "
                "within those folders is also accepted."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The question or topic to look for in the book."
                    },
                    "pdf_name": {
                        "type": "string",
                        "description": (
                            "Filename of the PDF to search, e.g. 'uvm primer.pdf'. "
                            "A full Windows path inside an allowed folder is also accepted."
                        )
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Number of relevant passages to return. Defaults to 3."
                    }
                },
                "required": ["query", "pdf_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_rtl",
            "description": (
                "Read numbered lines of an RTL source or constraint file inside "
                "D:/Youssef/Projects/agentic_ai_generated. Use this before edit_rtl "
                "to obtain the exact text to replace. Returns JSON with file path, "
                "total line count and numbered content."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Full path to an existing .v/.sv/.svh/.xdc/.sdc/.tcl file "
                            "inside the allowed project."
                        )
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "First line to read (1-based). Defaults to 1."
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "Last line to read, inclusive. Defaults to end of file; max 400 lines per call."
                    }
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_rtl",
            "description": (
                "Replace exactly one occurrence of a text snippet in an RTL file. "
                "Requires human approval. Creates a timestamped .bak before an atomic "
                "write. Returns JSON with status and backup path; errors if the snippet "
                "is not unique."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Full path to an existing RTL file inside the allowed project."
                    },
                    "old": {
                        "type": "string",
                        "description": "Exact existing text to replace; must occur exactly once in the file."
                    },
                    "new": {
                        "type": "string",
                        "description": "Replacement text."
                    }
                },
                "required": ["path", "old", "new"]
            }
        }
    },
        {
            "type": "function",
            "function": {
                "name": "write_rtl",
                "description": (
                    "Write a new RTL source or constraint file inside the given path"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Full path to an existing RTL file inside the allowed project."
                        },
                        "content": {
                            "type": "string",
                            "description": "New content for the file."
                        }
                    },
                    "required": ["path", "content"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "search_files",
                "description": (
                    "Search for files inside a folder: mode='files' lists file names "
                    "matching a glob pattern (e.g. '*.v' or '*counter*'); mode='content' "
                    "greps file contents for a regex and returns file paths with matching "
                    "line numbers and text. Read-only, capped at 200 matches."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "folder": {
                            "type": "string",
                            "description": "Folder to search (must exist and be inside an allowed root)."
                        },
                        "pattern": {
                            "type": "string",
                            "description": "Glob pattern for file names (mode='files') or regex for file contents (mode='content')."
                        },
                        "mode": {
                            "type": "string",
                            "enum": ["files", "content"],
                            "description": "'files' (default) matches file names; 'content' greps file contents."
                        }
                    },
                    "required": ["folder", "pattern"]
                }
            }
        },
    {
            "type": "function",
            "function": {
                "name": "rtl_debugger",
                "description": (
                    "Inspects the RTL around a failed behavioral simulation and returns "
                    "structured facts to find the cause of the error (module definition "
                    "files, module ports, instantiation port maps, unconnected ports, "
                    "unused testbench signals, and hints)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "testbench_path": {
                            "type": "string",
                            "description": "Full path to the testbench RTL file inside the allowed project."
                        },
                        "module_name": {
                            "type": "string",
                            "description": "Name of the top module the testbench instantiates ."
                        },
                        "errors": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional list of error strings from the simulation run."
                        },
                        "project_root": {
                            "type": "string",
                            "description": "Optional project root directory; defaults to the directory of the testbench file."
                        }
                    },
                    "required": ["testbench_path", "module_name"]
                }
            }
        },
    {
        "type": "function",
        "function": {
            "name": "run_testbench_iverilog",
            "description": (
                "Compile and run a Verilog/SystemVerilog testbench (and its RTL "
                "sources) using Icarus Verilog (iverilog + vvp). Returns JSON with "
                "status (pass/fail/error), exit code and the simulator output tail. Cannot run UVM testbenches"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Full paths of the testbench and RTL source files to "
                            "compile/run (must be inside the allowed project root)."
                        )
                    },
                    "top_module": {
                        "type": "string",
                        "description": (
                            "Optional top module name; passed to iverilog via -s."
                        )
                    },
                    "timeout": {
                        "type": "integer",
                        "description": (
                            "Timeout in seconds for the iverilog and vvp commands. "
                            "Defaults to 300."
                        )
                    }
                },
                "required": ["files"]
            }
        }
    },
    {

        "type": "function",
        "function": {
            "name": "run_testbench_vivado",
            "description": (
                "Compile and run a Verilog/SystemVerilog testbench (and its RTL "
                "sources) using Vivado. Returns JSON with "
                "status (pass/fail/error), exit code and the simulator output tail. Supports UVM testbenches"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Full paths of the testbench and RTL source files to "
                            "compile/run (must be inside the allowed project root)."
                        )
                    },
                    "timeout": {
                        "type": "integer",
                        "description": (
                            "Timeout in seconds. "
                            "Defaults to 300."
                        )
                    }
                },
                "required": ["files"]
            }
        }
    }
]