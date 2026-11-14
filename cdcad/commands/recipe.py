"""Recipe inspection, validation, planning, and export commands."""
from pathlib import Path

from .._common import CADError, atomic_bytes, document_path, json_bytes
from ..schema import recipe_schema


def register(s):
    s.add_parser("schema", help="Print the JSON Schema for resolved recipes").set_defaults(handler=schema)
    b = s.add_parser("build", help="Build a JSON recipe; use - for stdin")
    b.set_defaults(handler=build_command)
    b.add_argument("recipe", help="Recipe JSON file or - for stdin")
    b.add_argument("--base-dir", help="Resolve input paths here; defaults to recipe directory or cwd for stdin")
    b.add_argument("--out-dir", help="Root output directory; paths may not escape it")
    b.add_argument("--set", action="append", default=[], metavar="NAME=VALUE", help="Override a declared dimension; repeatable")
    b.add_argument("--target", action="append", help="Build only exports of this solid; repeatable")
    b.add_argument("--force", action="store_true", help="Replace existing model outputs")
    b.add_argument("--dry-run", action="store_true", help="List planned exports without evaluating geometry")
    b.add_argument("--report", help="Write build report JSON (overwritten atomically)")
    b.add_argument("--cache-dir", help="Content-addressed geometry cache; invalidates on code, dependencies, parameters and source bytes")
    v = s.add_parser("validate", help="Validate recipe schema and all references without building geometry")
    v.set_defaults(handler=validate)
    v.add_argument("recipe", help="Recipe JSON file or - for stdin")
    v.add_argument("--set", action="append", default=[], metavar="NAME=VALUE",
                   help="Override a declared dimension; repeatable")
    resolve = s.add_parser("resolve", help="Print the recipe with all dimensions evaluated")
    resolve.add_argument("recipe", help="Recipe JSON file or - for stdin")
    resolve.add_argument("--set", action="append", default=[], metavar="NAME=VALUE", help="Override a declared dimension; repeatable")
    resolve.set_defaults(handler=resolve_command)


def schema(args):
    return recipe_schema()


def resolve_command(args):
    from ..graph import validate_graph
    from ..recipe import read_recipe

    recipe = read_recipe(args.recipe, args.set)
    validate_graph(recipe)
    return recipe


def validate(args):
    resolve_command(args)
    return {"ok": True, "note": "Schema and graph valid; geometry and files not evaluated"}


def build_command(args):
    from ..build import build, recipe_source_paths
    from ..recipe import read_recipe

    recipe = read_recipe(args.recipe, args.set)
    base = Path.cwd() if args.recipe == "-" else Path(args.recipe).resolve().parent
    base = Path(args.base_dir).resolve() if args.base_dir else base
    recipe_files = {Path(args.recipe).resolve()} if args.recipe != "-" else set()
    if args.report:
        report_path = Path(args.report).resolve()
        source_paths = recipe_source_paths(recipe, base) | recipe_files
        model_paths = {(Path(args.out_dir or base)/document_path(e["path"])).resolve() for e in recipe["exports"]}
        if report_path in source_paths | model_paths:
            raise CADError("Report path conflicts with a source or model path")
    result = build(recipe, base, args.out_dir, args.target, args.force, args.dry_run, args.cache_dir,
                   protected_paths=recipe_files)
    if args.report:
        atomic_bytes(args.report, json_bytes(result))
    return result
