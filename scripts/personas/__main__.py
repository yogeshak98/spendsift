"""The persona tools, one command each.

    python -m scripts.personas build <slug> <out_dir>
    python -m scripts.personas qa [slug|all]
    python -m scripts.personas check --persona <slug> --data-dir <dir> [--live]
    python -m scripts.personas demo <slug> <workdir> [--port 8790]

`build` writes a persona's statements (and their truth sidecars) into
`out_dir` and prints the statement paths only. `qa` imports each persona
into a temporary data dir with the app's defaults, runs the rule book and
prints one table per persona; it exits 0 only when no rule FAILs and none
passes unexpectedly (PENDING is fine). `check` is `scripts.personas.check`.
`demo` prepares a live-demo workdir and prints the commands (see `demo_kit`).
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path


def _persona(parser: argparse.ArgumentParser, slug: str):
    from scripts.personas.catalog import ALL

    if slug not in ALL:
        parser.error(f"unknown persona {slug!r}; one of {sorted(ALL)}")
    return ALL[slug]


def _build(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    from scripts.personas.build import build

    persona = _persona(parser, args.slug)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for path in build(persona, args.out_dir):
        print(path)
    return 0


def _qa(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    from scripts.personas import qa
    from scripts.personas.catalog import ALL

    slugs = list(ALL) if args.slug == "all" else [args.slug]
    personas = [_persona(parser, slug) for slug in slugs]
    code = 0
    with tempfile.TemporaryDirectory(prefix="persona-qa-") as tmp:
        for persona in personas:
            run = qa.run_persona(persona, Path(tmp) / persona.slug)
            print(run.table())
            print()
            code = max(code, run.exit_code)
    print("qa: exit", code)
    return code


def _demo(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    from scripts.personas import demo_kit

    persona = _persona(parser, args.slug)
    try:
        demo_kit.check_port(args.port)
        demo_kit.prepare(persona, args.workdir, args.port)
    except (ValueError, FileExistsError) as err:
        print(f"demo: {err}", file=sys.stderr)
        return 2
    print(f"{persona.slug}: statements and a fresh data dir are ready.\n")
    print("\n".join(demo_kit.commands(persona, args.workdir, args.port)))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["check"]:
        from scripts.personas import check

        return check.main(argv[1:])
    parser = argparse.ArgumentParser(prog="python -m scripts.personas")
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="write a persona's statement files")
    b.add_argument("slug")
    b.add_argument("out_dir", type=Path)
    q = sub.add_parser("qa", help="import with app defaults and run the rule book")
    q.add_argument("slug", nargs="?", default="all")
    d = sub.add_parser("demo", help="prepare a live-demo workdir, print commands")
    d.add_argument("slug")
    d.add_argument("workdir", type=Path)
    d.add_argument("--port", type=int, default=8790)
    sub.add_parser("check", help="check a data dir (see scripts.personas.check)")
    args = parser.parse_args(argv)
    if args.command == "build":
        return _build(parser, args)
    if args.command == "demo":
        return _demo(parser, args)
    return _qa(parser, args)


if __name__ == "__main__":
    sys.exit(main())
