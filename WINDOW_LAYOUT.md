# Browser Window Layout

`run_app.bat` now opens the workspace as two separate Chrome/Edge windows instead
of two tabs in one window.

## Layout

- Left window: local monitoring app at `http://127.0.0.1:8000`
- Right window: Remember site from `REMEMBER_URL`
- Both windows use the same controlled browser profile from
  `REMEMBER_BROWSER_PROFILE_DIR`.
- Both windows share the same Chrome DevTools Protocol endpoint from
  `REMEMBER_CDP_URL`.

## Why

The operator needs to watch two things at the same time:

- the app-side progress, logs, API calls, costs, and candidate results
- the Remember-side browser actions such as scrolling, clicking, and page changes

Separate windows make this visible without switching tabs.

## Notes

- On Windows, the launcher reads the primary screen size and places the windows
  side by side.
- If Chrome/Edge ignores the requested position because of OS/window-manager
  behavior, the windows still open separately and can be manually adjusted.
- `open_browser_workspace.bat` uses the same layout without restarting the server.
