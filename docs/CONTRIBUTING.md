# Contributing & Development Guide

## Development Setup

### 1. Clone and Install

```bash
git clone https://github.com/FilipMichalkiewicz/flipper.git
cd flipper
pip install -r requirements.txt
```

### 2. Install mpv

- **macOS:** `brew install mpv`
- **Windows:** The app auto-downloads libmpv on first run, or manually place `libmpv-2.dll` in the project root
- **Linux:** `sudo apt install libmpv-dev` (or equivalent)

### 3. Run

```bash
python main.py
```

### Debug Mode

Enable the debug console via:
- Environment variable: `FLIPPER_DEBUG=1 python main.py`
- Settings tab → Debug Console checkbox (persists across sessions)

---

## Code Conventions

### Language

- Source code comments and variable names are in **English**
- UI strings and log messages are in **Polish** (the app targets Polish-speaking users)
- Documentation is in **English**

### Style

- No formatter or linter is configured. The codebase generally follows PEP 8 with some deviations:
  - Lines occasionally exceed 79 characters
  - Some functions are very long (should be refactored)
- Type hints are used in `scanner.py` (function signatures) but not consistently in `main.py`

### Naming

- Private methods: `_method_name()` (single underscore prefix)
- Constants: `UPPER_SNAKE_CASE`
- Instance variables: `self.lower_snake_case`
- GUI variables: `self.widget_name_var` for Tkinter variables

### Threading

- All I/O must run in background threads (never block the main thread)
- Use `self.root.after(0, callback)` to update GUI from background threads
- Use `self.stop_event.is_set()` to check for cancellation in loops
- Use `self.pause_event.wait()` to respect pause requests

---

## Project Structure Notes

### The Monolith Problem

`main.py` is ~4800 lines in a single class. This is the biggest technical debt item. If you're making significant changes, consider whether logic can be extracted into a separate module (like `scanner.py` was).

Candidate extractions:
- **Player logic** (~400 lines) → `player.py`
- **Proxy management UI** (~500 lines) → separate from scanning logic
- **Profile management** (~200 lines) → `profiles.py`
- **Auto-update** (~300 lines) → `updater.py`
- **Session persistence** (~200 lines) → `session.py`
- **GUI widget factories** (~100 lines) → `widgets.py`

### No Test Suite

There are no automated tests. Any refactoring should include adding tests. Key areas to test:
- `scanner.py` functions (unit testable — mock HTTP requests)
- Proxy pool thread safety (concurrent access tests)
- Session serialization/deserialization
- MAC generation and validation

---

## Known Issues & Technical Debt

### Type Safety

The LSP reports numerous type errors, primarily:
- `None` passed where `str` is expected (proxy and token parameters)
- Dynamic attributes on Tkinter widgets (`_normal_bg`, `_hover_bg`, `_command`)
- `subprocess.CREATE_NEW_PROCESS_GROUP` flagged on non-Windows

These are runtime-safe (guarded by platform checks and None handling) but should be cleaned up with proper `Optional` types and platform-gated imports.

### Architecture

- Single 4800-line file is hard to navigate and maintain
- No separation between GUI layer and business logic
- State management via instance variables becomes unwieldy at scale
- No dependency injection — hard to test in isolation

### Missing Features

- No test suite
- No CI/CD pipeline
- No automated linting
- No macOS auto-update (only Windows)
- Channel cache has no TTL/expiration
- No rate limiting on proxy fetch (could hammer APIs)

### Platform Gaps

- Auto-update only works on Windows
- DPAPI encryption only works on Windows (XOR fallback elsewhere)
- mpv DLL auto-download only in the Windows build script
- Some UI polish is Windows-centric

---

## Making Changes

### Adding a New Feature

1. Plan the feature — identify which files need changes
2. If it involves network I/O, add protocol logic to `scanner.py`
3. Add UI to `main.py` in the appropriate tab section
4. Wire up background threading with `stop_event` / `pause_event` support
5. Add persistence fields to session save/load if needed
6. Test manually on both macOS and Windows if possible

### Modifying the Scanner

`scanner.py` is the cleanest module. Functions are pure-ish (take parameters, return results) with the exception of the shared proxy state. When adding new portal API calls:

1. Follow the existing pattern: `make_cookies()` + `make_params()` + `requests.get()`
2. Handle `requests.exceptions.RequestException` broadly
3. Return structured data, let the caller handle UI
4. Add proxy parameter support for consistency

### Modifying the Build

- Test build changes on a clean machine if possible
- The Windows build script has many fallback paths — test the happy path AND failure recovery
- Update `BUILD_README.md` if the process changes
