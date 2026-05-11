from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
BACKEND_PYTHON = ROOT / "backend" / ".venv" / "bin" / "python"


def main() -> None:
    _ensure_playwright_python()
    backend_port = _free_port()
    frontend_port = _free_port()
    with tempfile.TemporaryDirectory(prefix="open-deepagents-e2e-") as tmp:
        tmpdir = Path(tmp)
        models_path = tmpdir / "models.json"
        models_path.write_text(json.dumps(_models_json()), encoding="utf-8")
        upload_file = tmpdir / "browser-e2e.txt"
        upload_file.write_text("BROWSER_UPLOAD_MARKER", encoding="utf-8")
        backend = _start_backend(backend_port, frontend_port, tmpdir, models_path)
        frontend = _start_frontend(frontend_port, backend_port)
        try:
            _wait_for(f"http://127.0.0.1:{backend_port}/ready")
            _wait_for(f"http://127.0.0.1:{frontend_port}")
            _run_browser(frontend_port, upload_file)
        finally:
            _terminate(frontend)
            _terminate(backend)


def _run_browser(frontend_port: int, upload_file: Path) -> None:
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(f"http://127.0.0.1:{frontend_port}", wait_until="networkidle")
            page.locator("#admin-password").fill("secret")
            page.get_by_role("button", name=re.compile("进入工作台|Enter workspace")).click()
            expect(page.get_by_role("button", name=re.compile("上传附件|Upload attachment"))).to_be_visible()

            with page.expect_response(
                lambda response: "/sessions/" in response.url
                and response.url.endswith("/uploads")
                and response.request.method == "POST"
            ) as upload_response:
                page.locator("input[type=file]").set_input_files(str(upload_file))
            upload_record = upload_response.value.json()
            assert upload_record["path"].startswith("/uploads/")
            assert re.match(r"^/uploads/[A-Za-z0-9]{8}/browser-e2e\.txt$", upload_record["path"])
            expect(page.get_by_text("browser-e2e.txt")).to_be_visible()
            expect(page.get_by_text(re.compile("已上传|Uploaded"))).to_be_visible()

            page.locator("textarea").fill("请读取刚才上传的附件，并说明里面的 marker。")
            with page.expect_request(
                lambda request: request.url.endswith("/runs/stream")
                and request.method == "POST"
            ) as run_request:
                page.get_by_role("button", name=re.compile("^发送$|^Send$")).click()
            run_payload = run_request.value.post_data_json
            assert run_payload["attachments"][0]["path"].startswith("/uploads/")
            expect(page.get_by_text(re.compile("Browser E2E read upload"))).to_be_visible(
                timeout=10_000
            )
            download_link = page.locator("a.attachment-download-button").first
            expect(download_link).to_be_visible()
            href = download_link.get_attribute("href") or ""
            assert "access_token=" in href
            assert page.request.get(href).status == 200

            page.reload(wait_until="networkidle")
            expect(page.get_by_role("button", name=re.compile("上传附件|Upload attachment"))).to_be_visible()
            expect(page.get_by_text(re.compile("Browser E2E read upload"))).to_be_visible(
                timeout=10_000
            )

            page.locator("textarea").fill("第二轮：请说明你是否还记得上一轮我让你读取附件。")
            with page.expect_request(
                lambda request: request.url.endswith("/runs/stream")
                and request.method == "POST"
            ):
                page.get_by_role("button", name=re.compile("^发送$|^Send$")).click()
            expect(
                page.get_by_text(
                    re.compile("Browser E2E prior prompt: 请读取刚才上传的附件，并说明里面的 marker。")
                )
            ).to_be_visible(timeout=10_000)
        finally:
            browser.close()


def _ensure_playwright_python() -> None:
    try:
        import playwright  # noqa: F401

        return
    except ModuleNotFoundError:
        pass

    if os.environ.get("OPEN_DEEPAGENTS_E2E_REEXEC") == "1":
        raise RuntimeError(
            "Python Playwright is required for browser E2E verification. "
            "Install it or set PLAYWRIGHT_PYTHON to an interpreter that has it."
        )

    for candidate in _playwright_python_candidates():
        if Path(candidate).resolve() == Path(sys.executable).resolve():
            continue
        if _python_has_playwright(candidate):
            env = os.environ.copy()
            env["OPEN_DEEPAGENTS_E2E_REEXEC"] = "1"
            os.execve(candidate, [candidate, __file__, *sys.argv[1:]], env)

    raise RuntimeError(
        "Python Playwright is required for browser E2E verification. "
        "Install it or set PLAYWRIGHT_PYTHON to an interpreter that has it."
    )


def _playwright_python_candidates() -> list[str]:
    candidates = [
        os.environ.get("PLAYWRIGHT_PYTHON", ""),
        shutil.which("python3") or "",
        shutil.which("python") or "",
        "/Users/zhaoquan/miniconda3/bin/python",
    ]
    return [candidate for candidate in dict.fromkeys(candidates) if candidate]


def _python_has_playwright(candidate: str) -> bool:
    try:
        result = subprocess.run(
            [candidate, "-c", "import playwright"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return False
    return result.returncode == 0


def _start_backend(
    backend_port: int,
    frontend_port: int,
    tmpdir: Path,
    models_path: Path,
) -> subprocess.Popen:
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(ROOT / "backend"),
            "DATABASE_URL": f"sqlite+pysqlite:///{tmpdir / 'browser-e2e.db'}",
            "ADMIN_USERNAME": "admin",
            "ADMIN_PASSWORD": "secret",
            "ADMIN_TOKEN_SECRET": "browser-e2e-token-secret-with-32-chars",
            "DEEPAGENTS_MODEL_CONFIG_PATH": str(models_path),
            "DEEPAGENTS_SANDBOX_PROFILE": "safe",
            "DEEPAGENTS_UPLOAD_ROOT_DIR": str(tmpdir / "uploads"),
            "CORS_ALLOWED_ORIGINS": f"http://127.0.0.1:{frontend_port}",
        }
    )
    return subprocess.Popen(
        [str(BACKEND_PYTHON), str(ROOT / "verification" / "e2e_backend_server.py"), str(backend_port)],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _start_frontend(frontend_port: int, backend_port: int) -> subprocess.Popen:
    env = os.environ.copy()
    env["VITE_API_BASE_URL"] = f"http://127.0.0.1:{backend_port}/api"
    return subprocess.Popen(
        ["npm", "run", "dev", "--", "--host", "127.0.0.1", "--port", str(frontend_port)],
        cwd=str(ROOT / "frontend"),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _wait_for(url: str, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=2) as response:
                if response.status < 500:
                    return
        except Exception as exc:
            last_error = exc
        time.sleep(0.25)
    raise RuntimeError(f"Timed out waiting for {url}: {last_error}")


def _terminate(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _models_json() -> dict[str, object]:
    return {
        "model": "test/fake",
        "provider": {
            "test": {
                "name": "Test",
                "options": {
                    "api_key": "test-key",
                    "base_url": "http://127.0.0.1/unused",
                },
                "models": {
                    "fake": {
                        "name": "Fake",
                        "model": "fake",
                    }
                },
            }
        },
    }


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"browser e2e failed: {exc}", file=sys.stderr)
        raise
