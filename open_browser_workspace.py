from __future__ import annotations

from run_app import (
    DEFAULT_CDP_URL,
    ROOT_DIR,
    ensure_env_file,
    launch_workspace_browser,
    read_env,
)


def main() -> None:
    ensure_env_file()
    env = read_env(ROOT_DIR / ".env")
    host = (env.get("APP_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    port = int(env.get("APP_PORT") or "8000")
    browser_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    app_url = f"http://{browser_host}:{port}"
    print(f"Opening workspace browser for {app_url}")
    print(f"CDP endpoint: {env.get('REMEMBER_CDP_URL') or DEFAULT_CDP_URL}")
    launch_workspace_browser(app_url, env)


if __name__ == "__main__":
    main()
