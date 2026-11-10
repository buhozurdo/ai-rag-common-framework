"""Unified Framework CLI (ai-rag / rag-mcp) (FEAT-11)."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional, Sequence

from common_framework._version import __version__
from common_framework.cli.doctor import run_doctor
from common_framework.cli.repl import CLIPlaygroundREPL
from common_framework.cli.scaffold import scaffold_project


def create_parser() -> argparse.ArgumentParser:
    """Construct command line interface parser."""
    parser = argparse.ArgumentParser(
        prog="ai-rag",
        description="AI RAG Common Framework — Enterprise Foundation CLI",
    )
    parser.add_argument(
        "-v", "--version",
        action="version",
        version=f"%(prog)s {__version__} (Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro})",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # 1. version
    subparsers.add_parser("version", help="Display framework version and runtime environment")

    # 2. doctor
    doctor_parser = subparsers.add_parser("doctor", help="Inspect environment, API keys, and service health")
    doctor_parser.add_argument("--json", action="store_true", help="Output diagnostic report in JSON format")
    doctor_parser.add_argument("--no-network", action="store_true", help="Skip network reachability tests")

    # 3. init
    init_parser = subparsers.add_parser("init", help="Bootstrap a new AI RAG microservice project")
    init_parser.add_argument("name", nargs="?", default="my-rag-service", help="Project name")
    init_parser.add_argument("--dir", default=".", help="Target directory for project scaffolding")

    # 4. run-server
    server_parser = subparsers.add_parser("run-server", help="Start FastAPI application server")
    server_parser.add_argument("--app", default="main:app", help="Application import path (default: main:app)")
    server_parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    server_parser.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000)")
    server_parser.add_argument("--reload", action="store_true", help="Enable auto-reload on code change")

    # 5. run-mcp
    mcp_parser = subparsers.add_parser("run-mcp", help="Run standalone Model Context Protocol (MCP) server")
    mcp_parser.add_argument("--transport", choices=["stdio", "sse"], default="stdio", help="MCP transport layer")
    mcp_parser.add_argument("--host", default="127.0.0.1", help="SSE host (default: 127.0.0.1)")
    mcp_parser.add_argument("--port", type=int, default=8080, help="SSE port (default: 8080)")
    mcp_parser.add_argument("--name", default="rag-mcp-server", help="MCP server identifier")

    # 6. repl
    subparsers.add_parser("repl", help="Launch interactive developer playground and tool terminal")

    return parser


def handle_version() -> int:
    print(f"ai-rag-common-framework {__version__}")
    print(f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro} ({sys.platform})")
    return 0


def handle_doctor(args: argparse.Namespace) -> int:
    report = run_doctor(check_network=not args.no_network)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.format_text())
    return 1 if report.has_failures else 0


def handle_init(args: argparse.Namespace) -> int:
    created = scaffold_project(target_dir=args.dir, project_name=args.name)
    print(f"Successfully bootstrapped project '{args.name}' in '{args.dir}':")
    for f in created:
        print(f"  + Created {f}")
    if not created:
        print("  (All starter files already exist in directory)")
    print("\nNext steps:")
    print("  1. Edit .env with your LLM credentials")
    print("  2. Run 'ai-rag doctor' to verify readiness")
    print("  3. Run 'ai-rag run-server' or 'python main.py'")
    return 0


def handle_run_server(args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError:
        print("Error: 'uvicorn' is required to run the HTTP server.", file=sys.stderr)
        print("Install with: pip install ai-rag-common-framework[api]", file=sys.stderr)
        return 1

    print(f"Starting API server on http://{args.host}:{args.port} (app={args.app})...")
    uvicorn.run(args.app, host=args.host, port=args.port, reload=args.reload)
    return 0


def handle_run_mcp(args: argparse.Namespace) -> int:
    try:
        from common_framework.mcp.server import create_mcp_server
    except ImportError:
        print("Error: MCP components are required. Install with: pip install ai-rag-common-framework[mcp]", file=sys.stderr)
        return 1

    server = create_mcp_server(name=args.name)
    if args.transport == "stdio":
        print(f"Starting MCP stdio server '{args.name}'...")
        server.run_stdio()
    else:
        try:
            import uvicorn
        except ImportError:
            print("Error: 'uvicorn' is required for SSE transport. Install with: pip install ai-rag-common-framework[api]", file=sys.stderr)
            return 1
        print(f"Starting MCP SSE server '{args.name}' on http://{args.host}:{args.port}...")
        sse_app = server.create_sse_app()
        uvicorn.run(sse_app, host=args.host, port=args.port)
    return 0


def handle_repl() -> int:
    repl = CLIPlaygroundREPL()
    repl.run()
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI application entry point."""
    parser = create_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    if args.command == "version":
        return handle_version()
    if args.command == "doctor":
        return handle_doctor(args)
    if args.command == "init":
        return handle_init(args)
    if args.command == "run-server":
        return handle_run_server(args)
    if args.command == "run-mcp":
        return handle_run_mcp(args)
    if args.command == "repl":
        return handle_repl()

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
