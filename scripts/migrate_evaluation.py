"""Explicitly upgrade only an isolated evaluation database; never the business database."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    args = parser.parse_args()
    os.environ["CISP_EVAL_DB_PATH"] = str(args.database.resolve())
    from storage.evaluation_workspace import migrate

    print(json.dumps(migrate()))


if __name__ == "__main__":
    main()
