from __future__ import annotations

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description="SuperLotto Plus modeling workflow")
    parser.add_argument("command", choices=["status", "generate", "score"])
    parser.add_argument("--draw-date")
    args = parser.parse_args()

    if args.command == "status":
        print("Scaffold installed. Implement source adapters and optimizer before production use.")
        return

    raise SystemExit(
        f"'{args.command}' is intentionally not implemented in the scaffold. "
        "Use Codex to complete source verification, modeling, and immutable logging first."
    )


if __name__ == "__main__":
    main()
