from __future__ import annotations

import argparse
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_long_memory.memory_db import CreateMemoryRecordInput  # noqa: E402
from agent_long_memory.semantic_memory import search_semantic_memory, write_semantic_memory  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manually debug workspace-scoped semantic memory.")
    parser.add_argument("--workspace", type=Path, default=Path.cwd(), help="Workspace path used to resolve workspace scope.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    write_parser = subparsers.add_parser("write", help="Write a semantic memory record.")
    write_parser.add_argument("--memory-type", required=True)
    write_parser.add_argument("--title", required=True)
    write_parser.add_argument("--content", required=True)
    write_parser.add_argument("--source-type")
    write_parser.add_argument("--source-ref")
    write_parser.add_argument("--module-path")
    write_parser.add_argument("--tag", action="append", dest="tags")
    write_parser.add_argument("--importance", type=float, default=0.5)
    write_parser.add_argument("--confidence", type=float, default=0.5)
    write_parser.add_argument("--no-embedding", action="store_true")

    search_parser = subparsers.add_parser("search", help="Search semantic memory records.")
    search_parser.add_argument("--query", required=True)
    search_parser.add_argument("--memory-type", action="append", dest="memory_types")
    search_parser.add_argument("--limit", type=int, default=8)

    args = parser.parse_args(argv)

    try:
        if args.command == "write":
            result = write_semantic_memory(
                args.workspace,
                CreateMemoryRecordInput(
                    memory_type=args.memory_type,
                    title=args.title,
                    content=args.content,
                    source_type=args.source_type,
                    source_ref=args.source_ref,
                    module_path=args.module_path,
                    tags=args.tags,
                    importance=args.importance,
                    confidence=args.confidence,
                ),
                create_embedding=not args.no_embedding,
            )
            print(f"project={result.project.id} root={result.project.root_path}")
            print(f"record={result.record.id} type={result.record.memory_type} title={result.record.title}")
            print(f"embedding={result.embedding.id if result.embedding else 'none'}")
            return 0

        if args.command == "search":
            results = search_semantic_memory(
                args.workspace,
                args.query,
                memory_types=args.memory_types,
                limit=args.limit,
            )
            for item in results:
                print(f"{item.similarity:.4f}\t{item.embedding_model}\t{item.record.id}\t{item.record.title}")
            return 0
    except Exception as exc:
        print(f"semantic memory debug failed: {exc}", file=sys.stderr)
        return 1

    parser.error(f"unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

