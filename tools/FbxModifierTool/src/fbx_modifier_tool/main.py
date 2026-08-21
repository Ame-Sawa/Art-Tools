from __future__ import annotations

import logging
import sys
import traceback
from pathlib import Path


def _resolve_log_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "launch.log"
    return Path(__file__).resolve().parents[2] / "launch.log"


def _configure_logging() -> Path:
    log_path = _resolve_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_path, mode="w", encoding="utf-8"),
        ],
        force=True,
    )
    return log_path


def _install_exception_logger() -> None:
    def _log_unhandled_exception(exc_type, exc_value, exc_tb) -> None:
        logging.exception(
            "Unhandled exception",
            exc_info=(exc_type, exc_value, exc_tb),
        )

    sys.excepthook = _log_unhandled_exception


def main() -> int:
    log_path = _configure_logging()
    _install_exception_logger()
    logging.info("FBX Modifier Tool start")
    logging.info("Python executable: %s", sys.executable)
    logging.info("Frozen: %s", getattr(sys, "frozen", False))
    logging.info("Log path: %s", log_path)

    try:
        from fbx_modifier_tool.app import run
    except Exception as exc:
        logging.exception("Failed to import application entry")
        print(f"启动失败：{exc}", file=sys.stderr)
        return 1

    try:
        exit_code = run()
        logging.info("Application exited with code: %s", exit_code)
        return exit_code
    except Exception as exc:
        logging.error("Application crashed: %s", exc)
        logging.error(traceback.format_exc())
        print(f"启动失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
