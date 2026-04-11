from __future__ import annotations

import sys


CLI_COMMANDS = {
    "menu",
    "login",
    "dialogs",
    "schedule",
    "list-scheduled",
    "cancel",
    "preview-import",
}


def _is_cli_mode(argv: list[str]) -> bool:
    if len(argv) <= 1:
        return False
    return argv[1] in CLI_COMMANDS


if _is_cli_mode(sys.argv):
    from app.main import main
else:
    if len(sys.argv) > 1 and sys.argv[1] == "gui":
        sys.argv.pop(1)
    from app.gui.app import main


if __name__ == "__main__":
    main()
