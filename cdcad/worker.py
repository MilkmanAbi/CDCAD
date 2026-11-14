"""Sequential JSON-lines command transport."""
import contextlib
import io
import json
import sys
import time

from ._common import CADError


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
            argv = request.get("args")
            if not isinstance(argv, list) or not all(isinstance(x, str) for x in argv):
                raise CADError("args must be an array of command-line strings")
            capture = io.StringIO()
            old_stdin = sys.stdin
            try:
                supplied = request.get("stdin", "")
                if not isinstance(supplied, str):
                    raise CADError("stdin must be a string")
                sys.stdin = io.StringIO(supplied)
                with contextlib.redirect_stdout(capture), contextlib.redirect_stderr(capture):
                    try:
                        args = command_parser.parse_args(argv)
                    except SystemExit as exc:
                        if exc.code == 0:
                            result = {"ok": True, "text": capture.getvalue()}
                        else:
                            raise CADError(capture.getvalue().strip()) from exc
                    else:
                        result = execute(args)
            finally:
                sys.stdin = old_stdin
            json.dumps(result, allow_nan=False)
            response = {"id": request_id, "ok": True, "result": result}
        except Exception as exc:
            response = {"id": request_id, "ok": False, "error": str(exc), "type": type(exc).__name__}
        response["seconds"] = round(time.perf_counter()-started, 6)
        print(json.dumps(response, allow_nan=False, separators=(",", ":")), flush=True)
    return 0
