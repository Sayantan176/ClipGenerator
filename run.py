"""
ClipGenerator — Application Launcher
Run this script to start the application:
    python run.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def ensure_env_file():
    """Ensure .env exists by checking credentials.txt or .env.example."""
    env_file = Path(".env")
    if not env_file.exists():
        cred_file = Path("credentials.txt")
        if cred_file.exists():
            print("Found credentials.txt, generating .env...")
            content = cred_file.read_text(encoding="utf-8").strip()
            # If it's just the key or KEY=value
            if "GROQ_API_KEY" not in content and content.startswith("gsk_"):
                content = f"GROQ_API_KEY={content}\n"
            Path(".env").write_text(
                content + "\nGROQ_MODEL=llama-3.3-70b-versatile\nGROQ_WHISPER_MODEL=whisper-large-v3\n",
                encoding="utf-8",
            )
        else:
            example = Path(".env.example")
            if example.exists():
                print("Creating .env from .env.example...")
                env_file.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")


def check_dependencies():
    """Check whether essential dependencies are installed."""
    missing = []
    for pkg in ["fastapi", "uvicorn", "groq", "sqlalchemy", "aiosqlite"]:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f"[!] Missing required Python packages: {', '.join(missing)}")
        print("    Please install them with:")
        print("    pip install -r requirements.txt\n")
        return False
    return True


def check_ffmpeg():
    """Check FFmpeg availability."""
    try:
        from backend.tasks.ffmpeg_utils import find_ffmpeg
        ffmpeg_bin = find_ffmpeg()
        print(f"[✓] FFmpeg detected: {ffmpeg_bin}")
    except Exception as e:
        print(f"[!] Warning: FFmpeg could not be found ({e}).")
        print("    You can install 'imageio-ffmpeg' (pip install imageio-ffmpeg)")
        print("    or install FFmpeg via winget: 'winget install Gyan.FFmpeg'")


def main():
    print("=" * 60)
    print("🎬  Starting ClipGenerator — AI Video Repurposing App")
    print("=" * 60)

    ensure_env_file()

    if not check_dependencies():
        sys.exit(1)

    check_ffmpeg()

    from backend.config import get_settings
    settings = get_settings()
    settings.ensure_dirs()

    if not settings.groq_api_key or not settings.groq_api_key.startswith("gsk_"):
        print("[!] Warning: GROQ_API_KEY not configured in .env.")
        print("    Add your free key from https://console.groq.com to .env for AI clip generation.\n")
    else:
        print("[✓] Groq API Key loaded successfully.")

    print(f"\n🚀 Server starting at: http://127.0.0.1:{settings.app_port}")
    print(f"📖 API Documentation: http://127.0.0.1:{settings.app_port}/api/docs")
    print("Press Ctrl+C to stop the server.\n")

    import uvicorn
    uvicorn.run(
        "backend.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=settings.debug,
    )


if __name__ == "__main__":
    main()
