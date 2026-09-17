import argparse

from funding_story.migration import run_flyway

parser = argparse.ArgumentParser(description="Run Funding Story AI database migrations")
parser.add_argument("command", choices=("info", "validate", "migrate"), nargs="?", default="migrate")
args = parser.parse_args()
run_flyway(args.command)
