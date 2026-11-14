"""Persistent CDCAD client. Run in an installed environment."""
import json
from pathlib import Path
import subprocess
import sys



def main():
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "worker-demo").resolve()
    with subprocess.Popen([sys.executable, "-m", "cdcad", "serve"],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          encoding="utf-8", bufsize=1) as worker:
        def command(request_id, *args):
            worker.stdin.write(json.dumps({"id": request_id, "args": list(map(str, args))})+"\n")
            worker.stdin.flush()
            line = worker.stdout.readline()
            if not line:
                raise RuntimeError("CDCAD worker stopped before replying")
            reply = json.loads(line)
            if reply["id"] != request_id:
                raise RuntimeError(f"CDCAD worker returned an unexpected request ID: {reply['id']}")
            print(json.dumps(reply, indent=2))
            if not reply["ok"]:
                raise RuntimeError(reply["error"])
            return reply["result"]
        try:
            if not (root/"project.json").exists():
                command("init", "project", "init", root, "--template", "arch-demo")
            command("initial", "project", "build", root)
            command("resize", "project", "set", root, "/params/span", "70")
            command("changed", "project", "build", root)
            command("unchanged", "project", "build", root)
        finally:
            worker.stdin.close()
            worker.wait()


if __name__ == "__main__":
    main()
