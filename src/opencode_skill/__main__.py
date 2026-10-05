"""Entry point so `python -m opencode_skill ...` works."""
from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
