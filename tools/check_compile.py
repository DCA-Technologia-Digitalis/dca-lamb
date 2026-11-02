from pathlib import Path
import py_compile
import sys
import traceback


def main():
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    try:
        py_compile.compile(str(app_path), doraise=True)
        print(f"OK: {app_path}")
    except Exception:
        print(f"ERR: {app_path}")
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
