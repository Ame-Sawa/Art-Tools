"""Run the batch UV GUI with ``python -m cli_anything.blender_gui``."""

try:
    from .app import main
except ModuleNotFoundError as exc:  # pragma: no cover - depends on local install
    if exc.name != "PySide6":
        raise
    raise SystemExit(
        "缺少 PySide6。请运行 launch_blender_uv_gui.bat，或执行 "
        "python -m pip install -e \".[gui]\"。"
    ) from exc


if __name__ == "__main__":
    main()
