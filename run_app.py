from __future__ import annotations

import importlib.util
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from urllib.parse import quote, urlparse


ROOT_DIR = Path(__file__).resolve().parent
ENV_PATH = ROOT_DIR / ".env"
ENV_EXAMPLE_PATH = ROOT_DIR / ".env.example"
REQUIREMENTS_PATH = ROOT_DIR / "requirements.txt"
DEFAULT_REMEMBER_URL = "https://career.rememberapp.co.kr/"
DEFAULT_CDP_URL = "http://127.0.0.1:9222"
DEFAULT_BROWSER_PROFILE_DIR = "browser_profile"


def main() -> None:
    os.chdir(ROOT_DIR)
    ensure_env_file()
    ensure_dependencies()

    env = read_env(ENV_PATH)
    host = env.get("APP_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = int(env.get("APP_PORT", "8000") or "8000")
    release_port(port)
    browser_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    url = f"http://{browser_host}:{port}"

    print(f"Starting server: {url}")
    print("Close this window to stop the server.")
    threading.Timer(1.2, lambda: launch_workspace_browser(url, env)).start()

    import uvicorn

    uvicorn.run("app.main:app", host=host, port=port, reload=False)


def ensure_env_file() -> None:
    if ENV_PATH.exists():
        return
    if ENV_EXAMPLE_PATH.exists():
        ENV_PATH.write_text(ENV_EXAMPLE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        return
    ENV_PATH.write_text(
        "\n".join(
            [
                "OPENAI_API_KEY=",
                "OPENAI_MODEL=gpt-5.4-mini",
                "APP_HOST=127.0.0.1",
                "APP_PORT=8000",
                "MIN_DELAY_SECONDS=1",
                "MAX_DELAY_SECONDS=3",
                "USD_KRW_RATE=1507.2",
                "CRAWLER_MODE=mock",
                f"REMEMBER_URL={DEFAULT_REMEMBER_URL}",
                f"REMEMBER_CDP_URL={DEFAULT_CDP_URL}",
                "REMEMBER_BROWSER_PORT=9222",
                f"REMEMBER_BROWSER_PROFILE_DIR={DEFAULT_BROWSER_PROFILE_DIR}",
                "BROWSER_LOCALE=ko-KR",
                "BROWSER_ACCEPT_LANGUAGE=ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
                "BROWSER_TIMEZONE=Asia/Seoul",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def ensure_dependencies() -> None:
    modules = ["fastapi", "uvicorn", "dotenv", "openai", "pydantic", "playwright"]
    missing = [name for name in modules if importlib.util.find_spec(name) is None]
    if not missing:
        return
    if not REQUIREMENTS_PATH.exists():
        raise RuntimeError("requirements.txt not found. Cannot install dependencies.")
    print(f"Installing missing dependencies: {', '.join(missing)}")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", str(REQUIREMENTS_PATH)])


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def release_port(port: int) -> None:
    if is_port_free(port):
        return
    pids = find_listening_pids(port)
    current_pid = os.getpid()
    pids = [pid for pid in pids if pid != current_pid]
    if not pids:
        raise RuntimeError(f"Port {port} is already in use, but no owning process was found.")
    print(f"Port {port} is already in use. Stopping existing process: {', '.join(map(str, pids))}")
    for pid in pids:
        stop_process(pid)
    wait_until_port_free(port)


def is_port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def find_listening_pids(port: int) -> list[int]:
    if os.name != "nt":
        return []
    output = subprocess.check_output(["netstat", "-ano", "-p", "tcp"], text=True, errors="ignore")
    pids: set[int] = set()
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        local_address, state, pid_text = parts[1], parts[3].upper(), parts[4]
        if state != "LISTENING":
            continue
        if local_address.rsplit(":", 1)[-1] != str(port):
            continue
        try:
            pids.add(int(pid_text))
        except ValueError:
            continue
    return sorted(pids)


def stop_process(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return
    os.kill(pid, 9)


def wait_until_port_free(port: int) -> None:
    import time

    deadline = time.time() + 8
    while time.time() < deadline:
        if is_port_free(port):
            return
        time.sleep(0.2)
    raise RuntimeError(f"Port {port} did not become available after stopping the existing process.")


def launch_workspace_browser(app_url: str, env: dict[str, str]) -> None:
    remember_url = (env.get("REMEMBER_URL") or DEFAULT_REMEMBER_URL).strip() or DEFAULT_REMEMBER_URL
    cdp_url = (env.get("REMEMBER_CDP_URL") or DEFAULT_CDP_URL).strip() or DEFAULT_CDP_URL
    debug_port = _debug_port(env, cdp_url)
    profile_dir = _profile_dir(env.get("REMEMBER_BROWSER_PROFILE_DIR") or DEFAULT_BROWSER_PROFILE_DIR)
    browser_path = find_browser_executable(env.get("BROWSER_EXECUTABLE"))

    if cdp_is_available(cdp_url):
        print(f"Using existing controlled browser: {cdp_url}")
        if browser_path:
            open_workspace_windows(browser_path, app_url, remember_url, debug_port, profile_dir, env, cdp_url)
        else:
            open_cdp_tab(cdp_url, app_url)
            open_cdp_tab(cdp_url, remember_url)
        return

    if not is_port_free(debug_port):
        print(f"Debug port {debug_port} is in use, but it is not a Chrome DevTools endpoint.")
        print("Opening only the app URL in the default browser.")
        webbrowser.open(app_url)
        return

    if not browser_path:
        print("Chrome or Edge was not found. Opening only the app URL in the default browser.")
        webbrowser.open(app_url)
        return

    open_workspace_windows(browser_path, app_url, remember_url, debug_port, profile_dir, env, cdp_url)


def open_workspace_windows(
    browser_path: Path,
    app_url: str,
    remember_url: str,
    debug_port: int,
    profile_dir: Path,
    env: dict[str, str],
    cdp_url: str,
) -> None:
    profile_dir.mkdir(parents=True, exist_ok=True)
    app_bounds, remember_bounds = workspace_window_bounds()
    print(f"Opening controlled browser on {cdp_url}")
    print(f"Browser profile: {profile_dir}")
    launch_browser_window(browser_path, app_url, debug_port, profile_dir, env, app_bounds)
    wait_until_cdp_available(cdp_url, timeout=4)
    launch_browser_window(browser_path, remember_url, debug_port, profile_dir, env, remember_bounds)
    time.sleep(1.2)
    arrange_workspace_windows(cdp_url, app_url, remember_url)


def launch_browser_window(
    browser_path: Path,
    target_url: str,
    debug_port: int,
    profile_dir: Path,
    env: dict[str, str],
    bounds: tuple[int, int, int, int],
) -> None:
    left, top, width, height = bounds
    command = [
        str(browser_path),
        f"--remote-debugging-port={debug_port}",
        "--remote-debugging-address=127.0.0.1",
        f"--user-data-dir={profile_dir}",
        f"--lang={(env.get('BROWSER_LOCALE') or 'ko-KR').strip() or 'ko-KR'}",
        "--no-first-run",
        "--no-default-browser-check",
        "--new-window",
        f"--window-position={left},{top}",
        f"--window-size={width},{height}",
        target_url,
    ]
    subprocess.Popen(command, cwd=str(ROOT_DIR), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def workspace_window_bounds() -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]]:
    screen_width, screen_height = screen_size()
    usable_height = max(700, screen_height - 80)
    app_width = max(640, screen_width // 2)
    remember_width = max(640, screen_width - app_width)
    if app_width + remember_width > screen_width:
        app_width = max(1, screen_width // 2)
        remember_width = max(1, screen_width - app_width)
    app_bounds = (0, 0, app_width, usable_height)
    remember_left = app_width
    remember_bounds = (remember_left, 0, remember_width, usable_height)
    return app_bounds, remember_bounds


def screen_size() -> tuple[int, int]:
    if os.name == "nt":
        try:
            import ctypes

            user32 = ctypes.windll.user32
            return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))
        except Exception:
            pass
    return 1920, 1080


def wait_until_cdp_available(cdp_url: str, timeout: float = 4) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cdp_is_available(cdp_url):
            return True
        time.sleep(0.2)
    return False


def arrange_workspace_windows(cdp_url: str, app_url: str, remember_url: str) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return

    app_bounds, remember_bounds = workspace_window_bounds()
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.connect_over_cdp(cdp_url)
        except Exception as exc:
            print(f"Could not arrange browser windows through CDP: {exc}")
            return
        try:
            pages = [page for context in browser.contexts for page in context.pages]
            app_page = find_workspace_page(pages, app_url, fallback="127.0.0.1")
            remember_page = find_workspace_page(pages, remember_url, fallback="remember")
            if app_page:
                set_page_window_bounds(app_page, app_bounds)
            if remember_page:
                set_page_window_bounds(remember_page, remember_bounds)
        finally:
            browser.close()


def find_workspace_page(pages, target_url: str, fallback: str):
    target = normalize_url_for_match(target_url)
    fallback = fallback.lower()
    for page in pages:
        if normalize_url_for_match(page.url).startswith(target):
            return page
    for page in pages:
        if fallback and fallback in page.url.lower():
            return page
    return None


def normalize_url_for_match(url: str) -> str:
    return url.rstrip("/").lower()


def set_page_window_bounds(page, bounds: tuple[int, int, int, int]) -> None:
    left, top, width, height = bounds
    try:
        session = page.context.new_cdp_session(page)
        window = session.send("Browser.getWindowForTarget")
        session.send(
            "Browser.setWindowBounds",
            {
                "windowId": window["windowId"],
                "bounds": {
                    "left": left,
                    "top": top,
                    "width": width,
                    "height": height,
                    "windowState": "normal",
                },
            },
        )
    except Exception as exc:
        print(f"Could not set window bounds for {page.url}: {exc}")


def _debug_port(env: dict[str, str], cdp_url: str) -> int:
    raw = env.get("REMEMBER_BROWSER_PORT")
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    parsed = urlparse(cdp_url)
    return int(parsed.port or 9222)


def _profile_dir(raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = ROOT_DIR / path
    return path


def cdp_is_available(cdp_url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{cdp_url.rstrip('/')}/json/version", timeout=0.6) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def open_cdp_tab(cdp_url: str, target_url: str) -> None:
    endpoint = f"{cdp_url.rstrip('/')}/json/new?{quote(target_url, safe='')}"
    request = urllib.request.Request(endpoint, method="PUT")
    try:
        urllib.request.urlopen(request, timeout=1.5).close()
    except (OSError, urllib.error.URLError) as exc:
        print(f"Could not open controlled browser tab for {target_url}: {exc}")


def find_browser_executable(configured: str | None = None) -> Path | None:
    candidates: list[str] = []
    if configured:
        candidates.append(configured)

    for command in ("msedge", "chrome", "chromium"):
        found = shutil.which(command)
        if found:
            candidates.append(found)

    program_files = [os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.environ.get("LocalAppData")]
    for base in [Path(value) for value in program_files if value]:
        candidates.extend(
            [
                str(base / "Microsoft" / "Edge" / "Application" / "msedge.exe"),
                str(base / "Google" / "Chrome" / "Application" / "chrome.exe"),
            ]
        )

    for candidate in candidates:
        path = Path(candidate).expanduser()
        if path.exists():
            return path
    return None


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"\nStartup failed: {exc}")
        input("Press Enter to close...")
        raise
