"""A Model Context Protocol server over stdio, so MCP clients can call CDCAD as tools.

It speaks JSON-RPC 2.0, one message per line, and implements only what a tool server
needs: initialize, ping, tools/list and tools/call. Tools run the same code as the CLI,
one at a time, with their output captured so nothing but protocol messages reaches stdout.
Register it with your client as a stdio server: command "cdcad", arguments ["mcp"].
"""
import base64
import json
import os
import sys
import tempfile

from ._common import VERSION, CADError, error_report

PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")

RECIPE_INPUT = {
    "recipe": {"type": "object", "description": "A recipe as a JSON object (see cdcad_guide)"},
    "path": {"type": "string", "description": "Or a recipe .json file path (relative to the server's directory)"},
    "set": {"type": "object", "description": "Parameter overrides, e.g. {\"w\": 80}",
            "additionalProperties": {"type": ["number", "string"]}},
    "base_dir": {"type": "string", "description": "Folder that input files in the recipe are relative to "
                                                  "(default: the recipe file's folder, or the server's directory)"},
}

TOOLS = [
    {"name": "cdcad_guide", "title": "CDCAD guide",
     "description": "The one-page manual: workflow, recipe skeleton, conventions, expressions and every operation. "
                    "Read it first.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "cdcad_describe", "title": "Describe an operation",
     "description": "Properties, defaults and a working example for one recipe operation, or the list of all "
                    "operations and expression functions when op is omitted.",
     "inputSchema": {"type": "object", "properties": {"op": {"type": "string"},
                                                      "kind": {"type": "string", "enum": ["profiles", "solids"]}}}},
    {"name": "cdcad_validate", "title": "Validate a recipe",
     "description": "Check a recipe's schema, references and expressions without meshing; returns warnings about "
                    "unused parameters and nodes. Errors include a hint about the fix.",
     "inputSchema": {"type": "object", "properties": dict(RECIPE_INPUT)}},
    {"name": "cdcad_build", "title": "Build a recipe",
     "description": "Mesh a recipe and write its exports into out_dir. Returns the report: per export volume, area, "
                    "bounds, extents, centre of mass, is_volume (closed solid), and check results.",
     "inputSchema": {"type": "object", "required": ["out_dir"], "properties": {
         **RECIPE_INPUT, "out_dir": {"type": "string", "description": "Folder for the exported files"},
         "force": {"type": "boolean", "description": "Replace existing files"},
         "target": {"type": "array", "items": {"type": "string"}, "description": "Only these exported solids"}}}},
    {"name": "cdcad_render", "title": "Render a preview",
     "description": "A PNG picture of a recipe solid (built in memory) or of a mesh file: iso, front, top and right "
                    "views by default. Use it to see what a recipe makes.",
     "inputSchema": {"type": "object", "properties": {
         **RECIPE_INPUT, "mesh": {"type": "string", "description": "A mesh file to draw instead of a recipe"},
         "solid": {"type": "string", "description": "Recipe solid (default: the first export)"},
         "view": {"type": "string", "enum": ["grid", "iso", "front", "back", "left", "right", "top", "bottom"]},
         "size": {"type": "integer", "minimum": 64, "maximum": 2048},
         "save_to": {"type": "string", "description": "Also keep the PNG at this path"}}}},
    {"name": "cdcad_inspect", "title": "Inspect a mesh file",
     "description": "Topology and measurements of an STL/3MF/OBJ/PLY/OFF/GLB file in mm; density (g/cm^3) adds "
                    "mass, bed [x, y, z] reports printer fit.",
     "inputSchema": {"type": "object", "required": ["path"], "properties": {
         "path": {"type": "string"}, "density": {"type": "number", "exclusiveMinimum": 0},
         "bed": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}}}},
    {"name": "cdcad_cli", "title": "Run any cdcad command",
     "description": "Run a cdcad command line (without the program name), e.g. [\"project\", \"build\", \"demo\"]. "
                    "stdin supplies input for arguments given as -.",
     "inputSchema": {"type": "object", "required": ["args"], "properties": {
         "args": {"type": "array", "items": {"type": "string"}}, "stdin": {"type": "string"}}}},
]


def _recipe_args(arguments, argv):
    """argv and stdin for a tool that takes a recipe object or path."""
    stdin = ""
    if ("recipe" in arguments) == ("path" in arguments):
        raise CADError("Give either recipe (an object) or path (a file)")
    if "recipe" in arguments:
        argv.append("-")
        stdin = json.dumps(arguments["recipe"])
    else:
        argv.append(str(arguments["path"]))
    for name, value in (arguments.get("set") or {}).items():
        argv += ["--set", f"{name}={json.dumps(value) if not isinstance(value, str) else value}"]
    if arguments.get("base_dir"):
        argv += ["--base-dir", str(arguments["base_dir"])]
    return argv, stdin


class Server:
    def __init__(self, command_parser, execute):
        from .worker import run_command
        self.run = lambda argv, stdin="": run_command(command_parser, execute, argv, stdin)

    def call(self, name, arguments):
        """Content blocks for a tool call."""
        if name == "cdcad_guide":
            return [{"type": "text", "text": self.run(["guide", "--text"])}]
        if name == "cdcad_describe":
            argv = ["describe"] + ([arguments["op"]] if arguments.get("op") else [])
            argv += ["--kind", arguments["kind"]] if arguments.get("kind") else []
            return [{"type": "text", "text": self.run(argv + ["--text"])}]
        if name == "cdcad_validate":
            argv, stdin = _recipe_args(arguments, ["validate"])
            return [{"type": "text", "text": json.dumps(self.run(argv, stdin))}]
        if name == "cdcad_build":
            argv, stdin = _recipe_args(arguments, ["build"])
            argv += ["--out-dir", str(arguments["out_dir"])]
            argv += ["--force"] if arguments.get("force") else []
            for target in arguments.get("target") or []:
                argv += ["--target", target]
            report = self.run(argv, stdin)
            report.pop("trace", None)
            return [{"type": "text", "text": json.dumps(report)}]
        if name == "cdcad_render":
            return self.render(arguments)
        if name == "cdcad_inspect":
            argv = ["inspect", str(arguments["path"])]
            if arguments.get("density"):
                argv += ["--density", str(arguments["density"])]
            if arguments.get("bed"):
                argv += ["--bed", *map(str, arguments["bed"])]
            return [{"type": "text", "text": json.dumps(self.run(argv))}]
        if name == "cdcad_cli":
            result = self.run(list(arguments.get("args", [])), arguments.get("stdin", ""))
            return [{"type": "text", "text": result if isinstance(result, str) else json.dumps(result)}]
        raise KeyError(name)

    def render(self, arguments):
        if "mesh" in arguments:
            argv, stdin = ["render", str(arguments["mesh"])], ""
        else:
            argv, stdin = _recipe_args(arguments, ["render"])
        if arguments.get("solid"):
            argv += ["--solid", arguments["solid"]]
        argv += ["--view", arguments.get("view", "grid"), "--size", str(arguments.get("size", 800))]
        with tempfile.TemporaryDirectory() as folder:
            target = arguments.get("save_to") or os.path.join(folder, "preview.png")
            summary = self.run(argv + ["-o", str(target), "--force"], stdin)
            with open(target, "rb") as f:
                data = base64.b64encode(f.read()).decode("ascii")
        if not arguments.get("save_to"):
            summary.pop("path", None)
        return [{"type": "image", "data": data, "mimeType": "image/png"}, {"type": "text", "text": json.dumps(summary)}]

    def handle(self, message):
        """The response to one JSON-RPC message, or None for a notification."""
        try:
            return self._handle(message)
        except Exception as exc:  # noqa: BLE001  one bad message must never stop the server
            if "id" not in message:
                return None
            return _error(message.get("id"), -32603, f"Internal error: {type(exc).__name__}: {exc}")

    def _handle(self, message):
        method, ident, params = message.get("method"), message.get("id"), message.get("params") or {}
        if "id" not in message:
            return None  # notifications (initialized, cancelled) need no answer
        if not isinstance(params, dict):
            return _error(ident, -32602, "params must be an object")
        if method == "initialize":
            requested = params.get("protocolVersion")
            version = requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
            result = {"protocolVersion": version, "capabilities": {"tools": {"listChanged": False}},
                      "serverInfo": {"name": "cdcad", "title": "CDCAD", "version": VERSION},
                      "instructions": "CDCAD builds 3D-printable meshes from JSON recipes. Call cdcad_guide first; "
                                      "use cdcad_render to look at a recipe before building it."}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            name = params.get("name")
            if name not in {tool["name"] for tool in TOOLS}:
                return _error(ident, -32602, f"Unknown tool: {name}")
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, dict):
                return _error(ident, -32602, "Tool arguments must be an object")
            tool = next(t for t in TOOLS if t["name"] == name)
            missing = [key for key in tool["inputSchema"].get("required", []) if key not in arguments]
            if missing:
                result = {"content": [{"type": "text", "text": json.dumps(
                    {"ok": False, "error": f"{name} needs {', '.join(missing)}", "type": "CADError"})}], "isError": True}
                return {"jsonrpc": "2.0", "id": ident, "result": result}
            try:
                result = {"content": self.call(name, arguments), "isError": False}
            except Exception as exc:  # tool failures are results the model can read and act on
                result = {"content": [{"type": "text", "text": json.dumps(error_report(exc))}], "isError": True}
        else:
            return _error(ident, -32601, f"Method not found: {method}")
        return {"jsonrpc": "2.0", "id": ident, "result": result}


def _error(ident, code, message):
    return {"jsonrpc": "2.0", "id": ident, "error": {"code": code, "message": message}}


def serve(command_parser, execute):
    server = Server(command_parser, execute)
    out = sys.stdout
    for line in getattr(sys.stdin, "buffer", sys.stdin):
        if isinstance(line, bytes):
            line = line.decode("utf-8-sig")
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            response = _error(None, -32700, "Parse error")
        else:
            if isinstance(message, list):  # JSON-RPC batches are not part of current MCP
                response = _error(None, -32600, "Batches are not supported")
            elif not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                response = _error(message.get("id") if isinstance(message, dict) else None, -32600, "Invalid request")
            else:
                response = server.handle(message)
        if response is not None:
            out.write(json.dumps(response, separators=(",", ":")) + "\n")
            out.flush()
    return 0
