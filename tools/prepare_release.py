"""Set truthful publication metadata for the repository chosen by its owner."""

import argparse
import json
import re
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True, help="GitHub owner/repository")
    parser.add_argument("--maintainer", required=True, help="GitHub username, with or without @")
    args = parser.parse_args()
    maintainer = args.maintainer.removeprefix("@")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repository):
        parser.error("Repository must be owner/repository")
    if not re.fullmatch(r"[A-Za-z0-9-]{1,39}", maintainer):
        parser.error("A valid GitHub username is required")
    root = Path(__file__).resolve().parents[1]
    path = root / "custom_components/fuelwatch_wa/manifest.json"
    manifest = json.loads(path.read_text())
    manifest.update(
        documentation=f"https://github.com/{args.repository}",
        issue_tracker=f"https://github.com/{args.repository}/issues",
        codeowners=[f"@{maintainer}"],
    )
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(
        f"Publication metadata configured for {args.repository}. Commit and publish to that repository."
    )


if __name__ == "__main__":
    main()
