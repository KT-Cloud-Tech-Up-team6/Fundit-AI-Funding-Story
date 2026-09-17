"""Adopt a pre-Flyway database after verifying its schema matches this release."""

import argparse

from funding_story.config import settings
from funding_story.migration import baseline_existing_database, inspect_schema


def main():
    parser = argparse.ArgumentParser(
        description="Verify an existing Funding Story schema and record Flyway baseline version 2"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the Flyway baseline marker after validation (default is inspection only)",
    )
    args = parser.parse_args()
    dsn = settings().database_dsn
    problems = inspect_schema(dsn)
    if problems:
        raise SystemExit("기존 DB 구조가 기준과 다릅니다:\n- " + "\n- ".join(problems))
    if not args.apply:
        print("기존 DB 구조 검증 성공. 실제 반영은 --apply를 추가하세요.")
        return
    baseline_existing_database(dsn)


if __name__ == "__main__":
    main()
