"""The MCP tool server, driven with a scripted JSON-RPC session."""
import base64
import json

import pytest
import subprocess
import sys

TORUS = {"version": 1, "solids": {"part": {"op": "torus", "major": 10, "minor": 3}},
         "exports": [{"solid": "part", "path": "torus.stl"}]}


def session(*messages, raw_lines=()):
    lines = [json.dumps(m) for m in messages] + list(raw_lines)
    result = subprocess.run([sys.executable, "-m", "cdcad", "mcp"], input="\n".join(lines) + "\n",
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return {r.get("id"): r for r in map(json.loads, result.stdout.splitlines())}


def call(ident, name, arguments):
    return {"jsonrpc": "2.0", "id": ident, "method": "tools/call", "params": {"name": name, "arguments": arguments}}


def test_handshake_and_tool_list():
    replies = session({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                       "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}},
                      {"jsonrpc": "2.0", "method": "notifications/initialized"},
                      {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                      {"jsonrpc": "2.0", "id": 3, "method": "ping"},
                      {"jsonrpc": "2.0", "id": 4, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}})
    assert replies[1]["result"]["protocolVersion"] == "2025-06-18"
    assert replies[1]["result"]["serverInfo"]["name"] == "cdcad"
    names = {tool["name"] for tool in replies[2]["result"]["tools"]}
    assert {"cdcad_guide", "cdcad_build", "cdcad_render", "cdcad_validate"} <= names
    assert replies[3]["result"] == {}
    assert replies[4]["result"]["protocolVersion"] == "2025-11-25"     # unknown version: offer the newest
    assert len(replies) == 4                                            # the notification got no reply


def test_build_render_and_errors_as_tool_results(tmp_path):
    replies = session(call(1, "cdcad_build", {"recipe": TORUS, "out_dir": str(tmp_path)}),
                      call(2, "cdcad_render", {"recipe": TORUS, "size": 128}),
                      call(3, "cdcad_validate", {"recipe": {"version": 1, "solids": {"p": {"op": "torsu"}}, "exports": []}}),
                      call(4, "cdcad_guide", {}),
                      call(5, "cdcad_nothing", {}),
                      {"jsonrpc": "2.0", "id": 6, "method": "resources/list"},
                      raw_lines=["{not json"])
    report = json.loads(replies[1]["result"]["content"][0]["text"])
    assert report["exports"][0]["is_volume"] and (tmp_path/"torus.stl").is_file()
    image, summary = replies[2]["result"]["content"]
    assert image["type"] == "image" and base64.b64decode(image["data"])[:4] == b"\x89PNG"
    # Exact B-rep torus, tessellated: the outer diameter is within the facet sag of 26.
    assert json.loads(summary["text"])["extents"] == pytest.approx([26.0, 26.0, 6.0], rel=1e-3)
    assert replies[3]["result"]["isError"]
    assert json.loads(replies[3]["result"]["content"][0]["text"])["hint"] == "Did you mean 'torus'?"
    assert replies[4]["result"]["content"][0]["text"].startswith("# CDCAD")
    assert replies[5]["error"]["code"] == -32602
    assert replies[6]["error"]["code"] == -32601
    assert replies[None]["error"]["code"] == -32700


def test_malformed_messages_do_not_stop_the_server():
    replies = session({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": [1, 2]},
                      call(2, "cdcad_build", {"recipe": TORUS}),
                      {"jsonrpc": "2.0", "id": 3, "method": "ping"})
    assert replies[1]["error"]["code"] == -32602
    assert replies[2]["result"]["isError"] and "out_dir" in replies[2]["result"]["content"][0]["text"]
    assert replies[3]["result"] == {}
