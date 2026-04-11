from __future__ import annotations

import sys


if len(sys.argv) > 1 and sys.argv[1] == "gui":
    from app.gui.app import main
else:
    from app.main import main


if __name__ == "__main__":
    main()
