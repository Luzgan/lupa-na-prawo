"""Write MCP configuration files for supported AI clients."""

import json
import platform
import subprocess
from pathlib import Path

from polish_law_helper.config import settings

MCP_URL = f"{settings.base_url}/mcp"
MCP_NAME = "lupa-na-prawo"
_MCP_ENTRY = {"url": MCP_URL}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _merge_mcp_config(config_path: Path, schema_key: str = "mcpServers") -> str:
    """Read existing config, set polish-law entry, write back. Returns status string."""
    data = _read_json(config_path)
    data.setdefault(schema_key, {})[MCP_NAME] = _MCP_ENTRY
    _write_json(config_path, data)
    return str(config_path)


def _check_mcp_in_file(path: Path, schema_key: str) -> bool:
    data = _read_json(path)
    return MCP_NAME in data.get(schema_key, {})


# ---------------------------------------------------------------------------
# Per-client installers
# ---------------------------------------------------------------------------

def install_claude_desktop() -> str:
    """Write MCP entry into Claude Desktop config."""
    system = platform.system()
    if system == "Darwin":
        config_path = Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    elif system == "Windows":
        config_path = Path.home() / "AppData" / "Roaming" / "Claude" / "claude_desktop_config.json"
    else:
        config_path = Path.home() / ".config" / "Claude" / "claude_desktop_config.json"
    return _merge_mcp_config(config_path, "mcpServers")


def install_claude_code() -> str:
    """Register via the claude CLI (project scope)."""
    # Remove stale registration (ignore errors), then re-add with HTTP transport
    subprocess.run(
        ["claude", "mcp", "remove", MCP_NAME],
        capture_output=True,
    )
    result = subprocess.run(
        ["claude", "mcp", "add", "--transport", "http", MCP_NAME, MCP_URL],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip() or "registered"


def install_cursor() -> str:
    """Write MCP entry into Cursor's global config."""
    config_path = Path.home() / ".cursor" / "mcp.json"
    return _merge_mcp_config(config_path, "mcpServers")


def install_windsurf() -> str:
    """Write MCP entry into Windsurf's config."""
    config_path = Path.home() / ".codeium" / "windsurf" / "mcp_config.json"
    return _merge_mcp_config(config_path, "mcpServers")


def install_vscode() -> str:
    """Write MCP entry into workspace-level .vscode/mcp.json."""
    config_path = Path.cwd() / ".vscode" / "mcp.json"
    return _merge_mcp_config(config_path, "servers")


# ---------------------------------------------------------------------------
# Bulk helpers
# ---------------------------------------------------------------------------

CLIENTS: dict[str, callable] = {
    "claude-desktop": install_claude_desktop,
    "claude-code": install_claude_code,
    "cursor": install_cursor,
    "windsurf": install_windsurf,
    "vscode": install_vscode,
}


def install_all() -> dict[str, str]:
    """Run all installers. Returns {client: 'ok: ...' | 'error: ...'}."""
    results = {}
    for name, fn in CLIENTS.items():
        try:
            msg = fn()
            results[name] = f"ok: {msg}"
        except Exception as e:
            results[name] = f"error: {e}"
    return results


def check_all_installed() -> dict[str, bool]:
    """Return {client: is_configured} for each supported client."""
    system = platform.system()
    if system == "Darwin":
        cd_path = Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    elif system == "Windows":
        cd_path = Path.home() / "AppData" / "Roaming" / "Claude" / "claude_desktop_config.json"
    else:
        cd_path = Path.home() / ".config" / "Claude" / "claude_desktop_config.json"

    return {
        "claude-desktop": _check_mcp_in_file(cd_path, "mcpServers"),
        "claude-code": _check_mcp_in_file(Path.cwd() / ".claude.json", "mcpServers"),
        "cursor": _check_mcp_in_file(Path.home() / ".cursor" / "mcp.json", "mcpServers"),
        "windsurf": _check_mcp_in_file(
            Path.home() / ".codeium" / "windsurf" / "mcp_config.json", "mcpServers"
        ),
        "vscode": _check_mcp_in_file(Path.cwd() / ".vscode" / "mcp.json", "servers"),
    }
