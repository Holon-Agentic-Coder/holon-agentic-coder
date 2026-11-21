"""``python -m sandbox_executor.entrypoint.executor``: the container entry the role dispatcher runs.

Runs the facade's `main`; an escaping exception prints its traceback and exits 1, while `main`'s own exit codes pass
through.
"""

import sys
import traceback

from sandbox_executor.entrypoint.executor import main

if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
