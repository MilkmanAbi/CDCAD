"""Sequential JSON-lines command transport."""
import contextlib
import io
import json
import sys
import time

from ._common import CADError, error_report


def run_command(command_parser, execute, argv, stdin=""):
    """Run one CLI command in this process with its own stdin, capturing anything it prints
    so that the transport's stdout stays clean. Returns the command's result; raises on error."""
    if not isinstance(argv, list) or not all(isinstance(x, str) for x in argv):
        raise CADError("args must be an array of command-line strings")
    if not isinstance(stdin, str):
        raise CADError("stdin must be a string")
    capture = io.StringIO()
    old_stdin = sys.stdin
    try:
        sys.stdin = io.StringIO(stdin)
        with contextlib.redirect_stdout(capture), contextlib.redirect_stderr(capture):
            try:
                args = command_parser.parse_args(argv)
            except SystemExit as exc:
                if exc.code == 0:
                    return {"ok": True, "text": capture.getvalue()}
                raise CADError(capture.getvalue().strip()) from exc
            result = execute(args)
    finally:
        sys.stdin = old_stdin
    json.dumps(result, allow_nan=False)
    return result


def serve(command_parser, execute):
    source = getattr(sys.stdin, 'buffer', sys.stdin)
    for line in source:
        if not line.strip():
            continue
        request_id = None
        started = time.perf_counter()
        try:
            if len(line) > 16*1024*1024:
                raise CADError("Request exceeds 16 MiB")

            def reject_constant(value):
                raise CADError(f"Invalid JSON number: {value}")
            if isinstance(line, bytes):
                line = line.decode("utf-8-sig")
            request = json.loads(line, parse_constant=reject_constant)
            if not isinstance(request, dict):
                raise CADError("Request must be an object with id and args")
            candidate = request.get("id")
            # Validate that the request ID can be echoed as strict JSON.
            json.dumps(candidate, allow_nan=False)
            request_id = candidate
            result = run_command(command_parser, execute, request.get("args"), request.get("stdin", ""))
            response = {"id": request_id, "ok": True, "result": result}
        except Exception as exc:
            response = {"id": request_id, **error_report(exc)}
        response["seconds"] = round(time.perf_counter()-started, 6)
        print(json.dumps(response, allow_nan=False, separators=(",", ":")), flush=True)
    return 0
