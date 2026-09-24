"""
PyInstaller Build Script for OpenSub Live.
Packages the entire application into a standalone Windows executable.
"""
import os
import subprocess
import sys
from pathlib import Path

def build():
    root_dir = Path(__file__).resolve().parent.parent
    main_py = root_dir / "src" / "main.py"

    print("========================================================")
    print("       Building OpenSub Live Standalone .exe")
    print("========================================================")

    cmd = [
        sys.executable,
        "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name", "OpenSubLive",
        "--windowed",  # No black terminal window
        "--collect-all", "faster_whisper",
        "--collect-all", "ctranslate2",
        "--collect-all", "PySide6",
        "--hidden-import", "websockets",
        "--hidden-import", "pyaudiowpatch",
        "--hidden-import", "av",
        "--hidden-import", "pynput",
        "--add-data", f"{root_dir / 'bridge'}{os.pathsep}bridge",
        str(main_py)
    ]

    print("Running command:\n", " ".join(cmd))
    res = subprocess.run(cmd, cwd=root_dir)

    if res.returncode == 0:
        print("\n========================================================")
        print("  Build completed successfully!")
        print(f"  Executable location: {root_dir / 'dist' / 'OpenSubLive'}")
        print("========================================================")
    else:
        print(f"\n[Error] Build failed with return code {res.returncode}")

if __name__ == "__main__":
    build()
