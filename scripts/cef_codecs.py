"""Prepare a pinned codec-enabled CEF SDK without installing foreign system packages."""

import hashlib
import json
import shutil
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path


def verified_package(package, cache):
    archive = cache / package["filename"]
    if not archive.is_file():
        with tempfile.NamedTemporaryFile(dir=cache, delete=False) as output:
            temporary = Path(output.name)
            try:
                for base in package["mirrors"]:
                    try:
                        with urllib.request.urlopen(
                            base + package["filename"], timeout=120
                        ) as source:
                            shutil.copyfileobj(source, output)
                        break
                    except urllib.error.HTTPError as error:
                        if error.code != 404:
                            raise
                else:
                    raise RuntimeError(
                        "Pinned codec package is unavailable: " + package["filename"]
                    )
                output.close()
                temporary.replace(archive)
            finally:
                temporary.unlink(missing_ok=True)
    with archive.open("rb") as source:
        actual = hashlib.file_digest(source, "sha256").hexdigest()
    if actual != package["sha256"]:
        raise RuntimeError("Codec package sha256 mismatch: " + str(archive))
    return archive


def extract_package(archive, destination):
    # Python 3.11 also supports these builds; use the distro's zstd executable.
    with tempfile.TemporaryFile() as uncompressed:
        subprocess.run(["zstd", "-dc", "--", str(archive)], stdout=uncompressed, check=True)
        uncompressed.seek(0)
        with tarfile.open(fileobj=uncompressed) as source:
            source.extractall(destination, filter="data")


def codec_packages(build, release):
    cache = build / "cef-codec-packages"
    cache.mkdir(exist_ok=True)
    # Verify every package before extracting or loading any native code, even
    # when reusing a prepared SDK. Never mix headers/resources from other CEFs.
    return [(p, verified_package(p, cache)) for p in release["runtime_packages"]]


def prepare_codec_sdk(sdk, build, release):
    packages = codec_packages(build, release)
    with tempfile.TemporaryDirectory(dir=build, prefix="cef-codecs-") as temporary:
        root = Path(temporary)
        for package, archive in packages:
            extracted = root / package["name"]
            extracted.mkdir()
            extract_package(archive, extracted)
            if package["name"] == "cef":
                header = (extracted / "usr/include/cef/include/cef_version.h").read_text()
                if f'#define CEF_VERSION "{release["version"]}"' not in header:
                    raise RuntimeError("Codec runtime does not match the SDK version")
                for directory in ("Release", "Resources"):
                    shutil.rmtree(sdk / directory)
                    (sdk / directory).mkdir()
                runtime = extracted / "usr/lib/cef"
                for item in runtime.iterdir():
                    if item.name == "chrome-sandbox":
                        continue
                    target = sdk / ("Release" if ".so" in item.name else "Resources") / item.name
                    if item.is_dir():
                        shutil.copytree(item, target)
                    else:
                        shutil.copy2(item, target)
            else:
                for filename in package["libraries"]:
                    shutil.copy2(extracted / "usr/lib" / filename, sdk / "Release" / filename)
            licenses = extracted / "usr/share/licenses"
            if licenses.is_dir():
                shutil.copytree(licenses, sdk / "Resources/codec-licenses", dirs_exist_ok=True)
        (sdk / "Resources/codec-runtime.json").write_text(json.dumps(release, indent=2) + "\n")
    missing = subprocess.run(
        ["ldd", str(sdk / "Release/libcef.so")],
        env={"LD_LIBRARY_PATH": str(sdk / "Release"), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )
    if missing.returncode or "not found" in missing.stdout:
        raise RuntimeError(
            "Missing codec runtime dependencies:\n" + missing.stdout + missing.stderr
        )
