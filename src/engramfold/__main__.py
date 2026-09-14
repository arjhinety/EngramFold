"""``python -m engramfold`` -- the CLI, reachable without the console script.

Present so that every supported invocation path performs the same work. A package whose
``-m`` form imports and exits 0 without running anything is indistinguishable from a
passing run, which is the failure this and the ``__main__`` guard in
``engramfold.registry.validate`` both exist to prevent.
"""

from __future__ import annotations

from engramfold.cli.main import main

if __name__ == "__main__":
    raise SystemExit(main())
