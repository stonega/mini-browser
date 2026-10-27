# GTK and Chromium

Chromium is now the main application's engine. All windows and controls remain
native GTK 4/libadwaita. The shared native bridge is in `native/cef`; this example
uses the real application with a temporary profile and no phone discovery.

```sh
./scripts/build-cef
/usr/bin/python3 examples/cef/browser.py https://www.youtube.com/
./scripts/smoke --test=cef
```

Normal browsing uses `./scripts/run` and retains app-local website sessions.
Build details and limitations: [setup](../../docs/implementation/setup.md).
