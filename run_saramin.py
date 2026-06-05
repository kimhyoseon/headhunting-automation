from __future__ import annotations

import os
import threading
from pathlib import Path

from run_app import (
    ENV_PATH,
    ROOT_DIR,
    ensure_dependencies,
    ensure_env_file,
    launch_workspace_browser,
    read_env,
    release_port,
)


SARAMIN_URL = "https://www.saramin.co.kr/zf_user/memcom/talent-pool/main/search"
SARAMIN_APP_PORT = "8001"
SARAMIN_BROWSER_PORT = "9232"
SARAMIN_CDP_URL = "http://127.0.0.1:9232"
SARAMIN_PROFILE_DIR = "%LOCALAPPDATA%\\headhunting-automation\\saramin_browser_profile"


def main() -> None:
    os.chdir(ROOT_DIR)
    ensure_env_file()
    ensure_dependencies()

    env = saramin_env(read_env(ENV_PATH))
    runtime_env_path = write_runtime_env(env)
    os.environ["HEADHUNTING_ENV_PATH"] = str(runtime_env_path)

    host = env.get("APP_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = int(env.get("APP_PORT", SARAMIN_APP_PORT) or SARAMIN_APP_PORT)
    release_port(port)
    browser_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    app_url = f"http://{browser_host}:{port}"

    print(f"Starting Saramin server: {app_url}")
    print(f"Saramin URL: {SARAMIN_URL}")
    print("Close this window to stop the server.")
    threading.Timer(1.2, lambda: launch_workspace_browser(app_url, env)).start()

    import uvicorn

    uvicorn.run("app.main:app", host=host, port=port, reload=False)


def saramin_env(base_env: dict[str, str]) -> dict[str, str]:
    env = dict(base_env)
    env.update(
        {
            "APP_PORT": SARAMIN_APP_PORT,
            "HEADHUNTING_PROVIDER": "saramin",
            "CRAWLER_MODE": "browser",
            # Temporary compatibility name: the shared browser launcher and
            # current backend still read REMEMBER_* until a provider layer is added.
            "REMEMBER_URL": SARAMIN_URL,
            "REMEMBER_CDP_URL": SARAMIN_CDP_URL,
            "REMEMBER_BROWSER_PORT": SARAMIN_BROWSER_PORT,
            "REMEMBER_BROWSER_PROFILE_DIR": SARAMIN_PROFILE_DIR,
        }
    )
    env.setdefault("APP_HOST", "127.0.0.1")
    return env


def write_runtime_env(env: dict[str, str]) -> Path:
    base_dir = Path(os.path.expandvars("%LOCALAPPDATA%\\headhunting-automation"))
    if "%LOCALAPPDATA%" in str(base_dir):
        base_dir = ROOT_DIR / ".runtime"
    base_dir.mkdir(parents=True, exist_ok=True)
    path = base_dir / "saramin.env"
    path.write_text("\n".join(f"{key}={value}" for key, value in env.items()) + "\n", encoding="utf-8")
    return path


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"\nSaramin startup failed: {exc}")
        input("Press Enter to close...")
        raise
