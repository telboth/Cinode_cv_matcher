from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


class CinodeTokenBootstrapError(Exception):
    pass


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _run_dir() -> Path:
    folder = _repo_root() / ".run"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _read_token_output(path: Path) -> tuple[str | None, list[str]]:
    if not path.exists():
        return None, []
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    token_value: str | None = None
    for line in lines:
        if line.lower().startswith("cinode_api_token="):
            token_value = line.split("=", 1)[1].strip()
            break
    return token_value, lines


def bootstrap_cinode_token_via_script(
    *,
    company_slug: str,
    api_account_name: str,
    timeout_ms: int,
) -> dict[str, Any]:
    script_path = _repo_root() / "apps" / "api" / "scripts" / "test_create_cinode_token.py"
    if not script_path.exists():
        raise CinodeTokenBootstrapError(f"Fant ikke script: {script_path}")

    with tempfile.TemporaryDirectory(prefix="cinode-token-bootstrap-") as tmp:
        output_file = Path(tmp) / "cinode-token-bootstrap.txt"
        cmd = [
            sys.executable,
            str(script_path),
            "--company",
            str(company_slug or "xlent"),
            "--name",
            str(api_account_name or "Cinode_key"),
            "--output-file",
            str(output_file),
            "--hide-token",
        ]

        try:
            completed = subprocess.run(
                cmd,
                cwd=str(_repo_root()),
                check=False,
                capture_output=True,
                text=True,
                timeout=max(15, int(timeout_ms / 1000)),
                env=os.environ.copy(),
            )
        except subprocess.TimeoutExpired as exc:
            raise CinodeTokenBootstrapError(f"Timeout ved token-bootstrap ({exc})") from exc
        except Exception as exc:
            raise CinodeTokenBootstrapError(f"Klarte ikke kjøre token-bootstrap-script: {exc}") from exc

        stdout_lines = (completed.stdout or "").splitlines()
        stderr_lines = (completed.stderr or "").splitlines()
        token_value, output_lines = _read_token_output(output_file)

        debug_trace: list[str] = []
        debug_trace.extend([f"SCRIPT: {line}" for line in stdout_lines[-80:]])
        debug_trace.extend([f"SCRIPT-ERR: {line}" for line in stderr_lines[-40:]])
        debug_trace.extend([f"OUTPUT: {line}" for line in output_lines[-40:]])

        if completed.returncode != 0:
            raise CinodeTokenBootstrapError(
                "Token-bootstrap script feilet. "
                + (stdout_lines[-1] if stdout_lines else "Se debug_trace for detaljer.")
            )

        if not token_value:
            raise CinodeTokenBootstrapError("Token-bootstrap fullført, men fant ikke 'cinode_api_token=' i output.")

        return {
            "ok": True,
            "token_value": token_value,
            "detail": "Cinode API-token ble opprettet via My account -> Api accounts.",
            "debug_trace": debug_trace,
        }
