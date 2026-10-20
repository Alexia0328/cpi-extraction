from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from .evaluation import EvaluationError, evaluate_live, evaluate_offline
from .extraction import ConfigurationError, ExtractionError
from .fetch import SourceError
from .pipeline import HarnessPaths, ValidationFailure, run_latest, run_month, run_url


EXIT_OK = 0
EXIT_USAGE = 2
EXIT_SOURCE = 3
EXIT_EXTRACTION = 4
EXIT_VALIDATION = 5
EXIT_EVALUATION = 6


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Repeated Statistics Canada monthly CPI extraction harness"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    latest = subparsers.add_parser("run-latest", help="discover and process the latest CPI release")
    latest.add_argument("--refresh", action="store_true", help="refetch the page even if output exists")

    run = subparsers.add_parser("run", help="process one supported CPI release")
    source = run.add_mutually_exclusive_group(required=True)
    source.add_argument("--url", help="English Statistics Canada Daily CPI URL")
    source.add_argument("--month", help="CPI reference month in YYYY-MM format")
    run.add_argument("--refresh", action="store_true", help="bypass existing page output")

    evaluate = subparsers.add_parser("eval", help="score the frozen 10-case evaluation set")
    mode = evaluate.add_mutually_exclusive_group(required=True)
    mode.add_argument("--offline", action="store_true", help="score frozen cached predictions")
    mode.add_argument(
        "--live",
        action="store_true",
        help="rerun all cases, using local Ollama only when fallback is needed",
    )
    return parser


def _print_summary(summary: object) -> None:
    if hasattr(summary, "model_dump"):
        summary = summary.model_dump(mode="json")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = Path.cwd()
    paths = HarnessPaths(root)
    try:
        if args.command == "run-latest":
            result = run_latest(paths, refresh=args.refresh)
            _print_summary({"status": result.status, "path": str(result.path)})
        elif args.command == "run":
            result = (
                run_month(args.month, paths, refresh=args.refresh)
                if args.month
                else run_url(args.url, paths, refresh=args.refresh)
            )
            _print_summary({"status": result.status, "path": str(result.path)})
        elif args.offline:
            _print_summary(evaluate_offline(root))
        else:
            _print_summary(evaluate_live(root))
        return EXIT_OK
    except ConfigurationError as exc:
        print(json.dumps({"event": "configuration_error", "error": str(exc)}))
        return EXIT_USAGE
    except SourceError as exc:
        print(json.dumps({"event": "source_error", "error": str(exc)}))
        return EXIT_SOURCE
    except ExtractionError as exc:
        print(json.dumps({"event": "extraction_error", "error": str(exc)}))
        return EXIT_EXTRACTION
    except ValidationFailure as exc:
        print(
            json.dumps(
                {"event": "validation_error", "error": str(exc), "path": str(exc.quarantine_path)}
            )
        )
        return EXIT_VALIDATION
    except (EvaluationError, ValueError) as exc:
        print(json.dumps({"event": "evaluation_error", "error": str(exc)}))
        return EXIT_EVALUATION
