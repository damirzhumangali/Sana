"""Evaluate a JSON semantic timeline; this command does not infer actions from video."""

import argparse
from pathlib import Path

from app.rules.engine import evaluate
from app.rules.schemas import RuleRequest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("timeline", type=Path)
    parser.add_argument("--output", type=Path, default=Path("data/rules/result.json"))
    args = parser.parse_args()
    if args.timeline.resolve() == args.output.resolve():
        parser.error("Output must differ from the input timeline")
    request = RuleRequest.model_validate_json(args.timeline.read_text(encoding="utf-8-sig"))
    report = evaluate(request)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    print(report.model_dump_json(indent=2))
    print(f"Saved: {args.output.resolve()}")


if __name__ == "__main__":
    main()
