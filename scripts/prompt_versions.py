"""Manage the day13-chat prompt versions/labels on Langfuse via the SDK.

    python scripts/prompt_versions.py create     # v1 (baseline, production) + v2 (candidate)
    python scripts/prompt_versions.py promote    # production -> v2
    python scripts/prompt_versions.py rollback   # production -> v1
    python scripts/prompt_versions.py show       # which version each label points to
"""
from __future__ import annotations

import argparse
import os

from dotenv import load_dotenv
from langfuse import get_client

load_dotenv(".env")
NAME = os.getenv("LANGFUSE_PROMPT_NAME", "day13-chat")
V1 = "Feature={{feature}}\nDocs={{docs}}\nQuestion={{message}}"
V2 = V1 + "\nAnswer in at most 3 sentences."


def show(lf) -> None:
    for label in ("baseline", "candidate", "production"):
        p = lf.get_prompt(NAME, label=label, cache_ttl_seconds=0)
        print(f"{label:>10} -> v{p.version}  labels={p.labels}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["create", "promote", "rollback", "show"])
    action = parser.parse_args().action
    lf = get_client()
    if action == "create":
        lf.create_prompt(name=NAME, type="text", prompt=V1, labels=["baseline", "production"], commit_message="v1 baseline")
        lf.create_prompt(name=NAME, type="text", prompt=V2, labels=["candidate"], commit_message="v2 shorter answers")
    elif action == "promote":
        lf.update_prompt(name=NAME, version=2, new_labels=["candidate", "production"])
    elif action == "rollback":
        lf.update_prompt(name=NAME, version=1, new_labels=["baseline", "production"])
    show(lf)


if __name__ == "__main__":
    main()
