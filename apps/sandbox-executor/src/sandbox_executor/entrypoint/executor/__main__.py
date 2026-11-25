import sys
import traceback

from sandbox_executor.entrypoint.executor import main

if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
