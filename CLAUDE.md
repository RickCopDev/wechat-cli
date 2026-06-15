# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

wechat-cli is a CLI tool that decrypts and queries local WeChat (微信) data. It extracts SQLCipher encryption keys from WeChat's process memory, decrypts the databases, and provides 11 subcommands for querying chats, contacts, messages, stats, etc. Designed as "AI-first" — JSON output by default, intended for LLM agent integration.

## Build & Run Commands

```bash
# Install in editable mode (development)
pip install -e .

# Run the CLI
wechat-cli init          # Extract keys and generate config
wechat-cli sessions      # List recent chat sessions
wechat-cli history       # Show chat history

# Build platform-specific standalone binaries (PyInstaller)
python npm/scripts/build.py [platform...]   # e.g. darwin-arm64, linux-x64

# No test suite or linting configuration exists in this project
```

## Architecture

### Data Flow
1. `init` scans WeChat process memory → extracts SQLCipher keys → saves to `~/.wechat-cli/`
2. All other commands load config + keys → create `AppContext` → `DBCache` decrypts databases on-the-fly
3. Decrypted pages cached in `/tmp/wechat_cli_cache/` with mtime-based staleness
4. Commands query decrypted SQLite via standard `sqlite3`

### Key Patterns
- **Click group**: `main.py` defines `@click.group()`, 11 subcommands registered via `cli.add_command()`
- **AppContext singleton**: `core/context.py` — initialized once per invocation, shared via `click.pass_context`
- **Platform strategy**: `keys/__init__.py` dispatches to `scanner_macos.py`, `scanner_linux.py`, or `scanner_windows.py` based on OS
- **Sharded message DBs**: WeChat stores messages across multiple `message_N.db` files; `find_msg_db_keys()` discovers and batches queries across all shards
- **Dual output**: Every command supports `--format json|text`; `output/formatter.py` centralizes dispatch
- **Fuzzy contact resolution**: `resolve_username()` does exact then substring match against display names

### Distribution
- **pip**: `wechat-cli` package on PyPI (Python ≥ 3.10, depends on click, pycryptodome, zstandard)
- **npm**: `@canghe_ai/wechat-cli` — platform-specific optional deps, `bin/wechat-cli.js` shim resolves and execs the PyInstaller binary
- **C binary**: `wechat_cli/bin/find_all_keys_macos.arm64` — macOS key extraction uses compiled C program; bundled via PyInstaller `--add-binary` and as package_data in pyproject.toml

### Module Map
- `commands/` — 11 Click subcommand implementations (init, sessions, history, search, contacts, members, export, stats, unread, favorites, new_messages)
- `core/` — config, context, crypto (SQLCipher AES-256-CBC page decryption + WAL), db_cache, key_utils, contacts, messages (largest module, ~791 lines)
- `keys/` — platform-dispatched key extraction; macOS uses C binary with auto re-sign fallback; Linux uses /proc/pid/mem; Windows uses Win32 ctypes
- `output/` — JSON/text output formatter
