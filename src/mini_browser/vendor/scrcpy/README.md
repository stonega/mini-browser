# Bundled Android mirroring server

This directory contains the unmodified, architecture-independent Android server
from scrcpy 5.0.1. Mini Browser uses ADB to run it temporarily on an authorized
phone. Display and basic input are handled by Mini Browser, so the desktop
scrcpy executable is not needed. No helper is downloaded at runtime.

`manifest.json` records the upstream release URL, source, size, and SHA-256.
The checksum was checked against both the GitHub release asset digest and the
release's `SHA256SUMS.txt`. `scrcpy_bundle.py` pins and verifies this checksum
before every connection. Preserve the Apache-2.0 license and attribution notice
when distributing the bundle.

To update, choose an explicit upstream release, verify its server against the
published checksums, replace this binary and manifest, and update the version
and SHA-256 in `scrcpy_bundle.py`. Review the server options and dependency
notices, run the deterministic checks and isolated phone smoke test, and validate
compatibility on consenting test devices. The APK/Dex file runs on Android;
Mini Browser's Linux package remains architecture-independent.
