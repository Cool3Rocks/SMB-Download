"""Bundle SMB dependencies for Linux and create a Decky install ZIP (run after frontend build)."""
import argparse
import pathlib
import subprocess
import sys
import tempfile
import zipfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--python-version", default="3.11", help="Decky backend Python version")
    args = parser.parse_args()
    root = pathlib.Path(__file__).resolve().parents[1]
    if not (root / "dist/index.js").exists():
        raise SystemExit("Build the frontend first: pnpm run build:js")
    vendor = root / "py_modules/smb_download_vendor"
    if vendor.exists():
        manifest = vendor / "bundled-wheels.txt"
        if not manifest.exists() or not manifest.read_text(encoding="utf-8").startswith("Decky Python: " + args.python_version + "\n"):
            raise SystemExit("Existing SMB Download dependencies target another Python version; build in a fresh folder")
    # Download only Linux wheels, even when building on Windows. Never install into system Python.
    with tempfile.TemporaryDirectory(prefix="smb-download-") as directory:
        if not vendor.exists():
            bundle_vendor(root, vendor, directory, args.python_version)
    output = root / "artifacts"
    output.mkdir(exist_ok=True)
    package = output / "SMB-Download.zip"
    with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in ("main.py", "plugin.json", "package.json"):
            archive.write(root / name, "SMB-Download/" + name)
        for name in ("smb_download_service.py", "smb_download_plugin.py", "smb_download_dependencies.py", "smb_download_power.py"):
            archive.write(root / "py_modules" / name, "SMB-Download/py_modules/" + name)
        stdlib = root / "py_modules/smb_download_stdlib"
        required_stdlib = ("logging/config.py", "socketserver.py", "configparser.py", "LICENSE")
        for name in required_stdlib:
            if not (stdlib / name).is_file():
                raise SystemExit("Missing frozen-runtime stdlib fallback: " + name)
            archive.write(stdlib / name, "SMB-Download/py_modules/smb_download_stdlib/" + name)
        for path in vendor.rglob("*"):
            # Windows-host pip may have fetched Windows-only pyspnego dependencies.
            # They are never used on SteamOS and don't belong in the Linux ZIP.
            if path.is_file() and not any(part.startswith("sspilib") for part in path.relative_to(vendor).parts):
                archive.write(path, "SMB-Download/" + path.relative_to(root).as_posix())
        archive.write(root / "dist/index.js", "SMB-Download/dist/index.js")
    print(package)


def bundle_vendor(root, vendor, directory, python_version):
    subprocess.run([sys.executable, "-m", "pip", "download", "--only-binary=:all:", "--no-deps",
                    "--platform", "manylinux_2_28_x86_64", "--platform", "manylinux2014_x86_64",
                    "--python-version", python_version, "--implementation", "cp",
                    "--dest", directory, "-r", str(root / "requirements-smb-download.txt")], check=True)
    vendor.mkdir()
    wheels = sorted(pathlib.Path(directory).glob("*.whl"))
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as archive:
            archive.extractall(vendor)
    (vendor / "bundled-wheels.txt").write_text(
        "Decky Python: " + python_version + "\n" + "\n".join(w.name for w in wheels), encoding="utf-8")


if __name__ == "__main__":
    main()
