"""CDCAD command discovery, dispatch, and output."""
import argparse
import json
from pathlib import Path
import sys

from ._common import CADError, EXPORT_FORMATS, UNITS, VERSION, dependency_versions, error_report
from .commands import docs, mesh, model, project, recipe, view
from .schema import PROFILE_OPS, SOLID_OPS


class ArgumentParser(argparse.ArgumentParser):
    """Use full option names so adding a flag cannot break existing scripts."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, allow_abbrev=False, **kwargs)


def parser():
    application = ArgumentParser(
        prog="cdcad",
        description="CDCAD | Command-line driven CAD for parametric models, assemblies, and mesh editing.",
        epilog="Run cdcad COMMAND --help for options and examples.",
    )
    application.add_argument("--version", action="version", version=f"CDCAD {VERSION}")
    application.add_argument("--debug", action="store_true", help="Show Python traceback on errors")
    application.add_argument("--pretty", action="store_true", help="Indent JSON output for reading")
    commands = application.add_subparsers(dest="command", required=True, metavar="COMMAND")
    model.register(commands)
    recipe.register(commands)
    mesh.register(commands)
    view.register(commands)
    docs.register(commands)
    project_commands = project.add_parser(commands)
    discovery = commands.add_parser("capabilities", help="List commands, modeling operations, and supported formats")
    discovery.set_defaults(handler=capabilities, command_choices=commands.choices,
                           project_choices=project_commands.choices)
    commands.add_parser("serve", help="Run a persistent JSON-lines process on stdin/stdout")
    commands.add_parser("mcp", help="Run a Model Context Protocol tool server on stdin/stdout (for MCP clients)")
    return application


def capabilities(args):
    from .expr import FUNCTIONS
    from .render import VIEWS
    return {
        "ok": True, "version": VERSION, "package": str(Path(__file__).resolve().parent),
        "recipe_version": 1, "project_version": 1,
        "profiles": list(PROFILE_OPS), "solids": list(SOLID_OPS),
        "commands": list(args.command_choices), "project_commands": list(args.project_choices),
        "units": dict(UNITS), "export_formats": list(EXPORT_FORMATS),
        "views": ["grid", *VIEWS], "functions": list(FUNCTIONS),
        "dependencies": dependency_versions(),
    }


def execute(args):
    if args.command in ("serve", "mcp"):
        raise CADError(f"{args.command} is a top-level process mode and cannot be nested")
    return args.handler(args)


def main(argv=None):
    application = parser()
    args = application.parse_args(argv)
    try:
        if args.command == "serve":
            from .worker import serve
            return serve(application, execute)
        if args.command == "mcp":
            from .mcp import serve
            return serve(application, execute)
        indent = 2 if args.pretty else None
        separators = None if args.pretty else (",", ":")
        result = execute(args)
        if isinstance(result, docs.Text):
            sys.stdout.write(result)
        else:
            print(json.dumps(result, indent=indent, separators=separators, allow_nan=False))
        return 0
    except KeyboardInterrupt:
        print(json.dumps({"ok": False, "error": "Interrupted", "type": "KeyboardInterrupt"}), file=sys.stderr)
        return 130
    except Exception as exc:
        if args.debug:
            raise
        print(json.dumps(error_report(exc)), file=sys.stderr)
        return 2
