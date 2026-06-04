"""Allow `python -m run_page.analysis.narrative` to invoke the CLI."""
from .cli import main

raise SystemExit(main())
