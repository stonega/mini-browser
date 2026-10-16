# Mini Browser

Native GNOME browser: Python, GTK 4, libadwaita, and WebKitGTK 6.0. Keep the
browsing surface limited to a native header bar (page title and close) and web
content. Each page owns a
window; the default size is 420 × 780 logical pixels.

## Layout and workflow

- `src/mini_browser/`: application, native UI, storage, and Chrome importer.
- `tests/`: deterministic unit tests and a local-page GTK/WebKit smoke test.
- `data/`: desktop integration and app icon.
- `scripts/`: run, check, and local installation helpers.
- `docs/design/`, `docs/implementation/`, `docs/user/`: architecture, setup,
  and usage. Read relevant docs before changes; update them with behavior.
- `examples/`: runnable examples. `postmortem/`: incident reports if needed.

Use the system Python and GI bindings. This is a native application; do not add
a web UI framework or JavaScript build pipeline. Keep core import/storage logic
independent of GTK. Use standard-library unittest and Ruff. Run
`./scripts/check` before finishing. Run `./scripts/smoke` on a graphical session
when modifying the UI. Do not test against real Chrome profiles or import the
user's private data during development; use generated temporary fixtures.

## Implementation rules

Keep functions small, dependencies explicit, and errors actionable. Preserve
Chrome source files. Import on a background thread; GTK work stays on the main
thread. Support repeated imports without duplicate records. Never log browsing
data, passwords, cookies, or profile contents. Keep TLS verification and the
WebKit sandbox enabled. Document import limitations honestly.

Passwords use an app-local six-digit PIN vault, not GNOME Keyring. Keep
credential metadata and secrets encrypted at rest. Use the established PyCA
Argon2id/AES-GCM format, atomic ciphertext writes and stale-write protection.
Never log secrets or interpolate them into JavaScript. Filling requires a user
action, exact origin and document checks, and an isolated WebKit world. Test
tampering, failed writes, lock/unlock and real WebKit filling with synthetic data.
Existing longer master passwords remain valid for unlocking old vaults; creation
and credential changes require exactly six ASCII digits. Preserve leading zeroes.

RPM packaging lives in `packaging/`; `./scripts/build-rpm` writes packages to
`dist/`. RPMs install the launcher in `/usr/bin` and private modules under
`/usr/share/mini-browser`. Never include personal browser data in a package.

## CodeGraph

Use the configured CodeGraph tools for structural questions and before edits
when an index is available. Trust its AST results. Prefer one focused context or
explore call over repeated symbol reads; use `rg` for literal text. If no index
exists, ask before running `codegraph init -i`. Do not delegate exploration.

## Documentation lookup and version control

Use Context7 to resolve the library and query current documentation for GNOME
APIs or other library/CLI questions. Prefer installed GI metadata and official
GNOME/WebKit documentation as supporting references. Prefer `git` over `gh`.
Run checks, inspect staged changes, and keep generated/private files out of Git.
