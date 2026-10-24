#!/usr/bin/python3
"""Synthetic helper executables used only with an explicit temporary fixture root."""

import json
import os
import re
import shlex
import shutil
import socket
import sys
import time
from pathlib import Path


def main():
    root = Path(os.environ["MINI_BROWSER_PHONE_FIXTURE"])
    tool = Path(sys.argv[0]).name
    arguments = sys.argv[1:]
    (root / f"{tool}-{os.getpid()}.pid").write_text(str(os.getpid()))
    if tool == "adb":
        if arguments == ["devices", "-l"]:
            records = root / "devices.json"
            phones = (
                json.loads(records.read_text())
                if records.exists()
                else [["synthetic-phone", "Synthetic Phone", "device"]]
            )
            print("List of devices attached")
            for address, model, state in phones:
                print(f"{address} {state} model:{model.replace(' ', '_')}")
            return
        allowed = {"synthetic-phone"}
        if (root / "devices.json").exists():
            allowed.update(record[0] for record in json.loads((root / "devices.json").read_text()))
        assert arguments[0] == "-s" and arguments[1] in allowed, (
            "Only generated phones may be tested"
        )
        address = arguments[1]
        arguments = arguments[2:]
        if arguments == ["get-state"]:
            if (root / "unauthorized").exists():
                print("synthetic private helper diagnostic")
                raise SystemExit(1)
            print("device")
        elif arguments[:2] == ["shell", "wm"]:
            print("Physical size: 1080x2160")
        elif arguments[:2] == ["shell", "getprop"]:
            identities = root / "identities.json"
            known = (
                json.loads(identities.read_text())
                if identities.exists()
                else {"synthetic-phone": "synthetic-phone"}
            )
            print(known.get(address, ""))
        elif arguments[0] == "push":
            shutil.copyfile(arguments[1], root / "pushed-server")
        elif arguments[:2] == ["forward", "tcp:0"]:
            with socket.socket() as connection:
                connection.bind(("127.0.0.1", 0))
                port = connection.getsockname()[1]
            (root / "android-port").write_text(str(port))
            print(port)
        elif "app_process" in arguments:
            version = arguments[arguments.index("com.genymobile.scrcpy.Server") + 1]
            (root / "server-version").write_text(version)
            with socket.socket() as server:
                server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                server.bind(("127.0.0.1", int((root / "android-port").read_text())))
                server.listen(1)
                connection, _ = server.accept()
                data = (root / "fixture.h264").read_bytes()
                with connection:
                    try:
                        if (root / "slow-frame").exists():
                            time.sleep(0.8)
                        while not (root / "android-end").exists():
                            for index in range(0, len(data), 2048):
                                connection.sendall(data[index : index + 2048])
                                time.sleep(0.03)
                    except (BrokenPipeError, ConnectionResetError):
                        pass
        elif arguments[:2] == ["shell", "input"]:
            remote_arguments = shlex.split(" ".join(arguments[2:]))
            with (root / "inputs.jsonl").open("a") as output:
                output.write(json.dumps(remote_arguments) + "\n")
    elif tool == "uxplay":
        if arguments == ["-h"]:
            print("-vrtp pipeline -pin code -reg filename")
            return
        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst

        Gst.init(None)
        pipeline_argument = arguments[arguments.index("-vrtp") + 1]
        port = re.search(r"port=(\d+)", pipeline_argument)[1]
        pipeline = Gst.parse_launch(
            "videotestsrc is-live=true pattern=ball "
            "! video/x-raw,width=240,height=480,framerate=15/1 "
            "! openh264enc bitrate=200000 gop-size=15 "
            "! rtph264pay config-interval=1 pt=96 "
            f"! udpsink host=127.0.0.1 port={port}"
        )
        pipeline.set_state(Gst.State.PLAYING)
        while True:
            pipeline.set_state(
                Gst.State.PAUSED if (root / "iphone-paused").exists() else Gst.State.PLAYING
            )
            time.sleep(0.1)


if __name__ == "__main__":
    main()
