"""Build reproducible source, Home Assistant install and optional card archives."""

import argparse
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".superpowers",
    "dist",
    "test-results",
    ".coverage",
}


def write_archive(path, items):
    with ZipFile(path, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for name, content in sorted(items.items()):
            info = ZipInfo(name, date_time=(2026, 9, 29, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content)
    with ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise RuntimeError(f"Corrupt archive: {path}")
    print(f"{path}: {path.stat().st_size} bytes, {len(items)} files")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    source = {}
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if (
            not path.is_file()
            or path.is_symlink()
            or output in path.parents
            or EXCLUDED.intersection(relative.parts)
            or path.suffix in {".pyc", ".zip"}
        ):
            continue
        source[relative.as_posix()] = path.read_bytes()
    write_archive(output / "FuelWatch-WA-Source.zip", source)
    install = {
        key: value
        for key, value in source.items()
        if key.startswith(("custom_components/", "blueprints/"))
        or key in {"README.md", "LICENSE", "VERIFICATION.md"}
    }
    write_archive(output / "FuelWatch-WA-Install.zip", install)
    card = {
        Path(key).name: value
        for key, value in source.items()
        if key.startswith("custom_components/fuelwatch_wa/www/")
    }
    card["LICENSE"] = source["LICENSE"]
    card["hacs.json"] = (
        json.dumps(
            {
                "name": "FuelWatch WA Card",
                "content_in_root": True,
                "filename": "fuelwatch-wa-card.js",
                "homeassistant": "2026.9.4",
                "country": ["AU"],
            },
            indent=2,
        )
        + "\n"
    ).encode()
    card["README.md"] = (
        b"# FuelWatch WA Card\n\nRequires the FuelWatch WA integration. "
        b"Its sidebar and bundled card already work without this separate package.\n\n"
        b"To publish separately, put these files in a public GitHub repository and add it "
        b"as a HACS Dashboard repository. Register the resulting JavaScript module resource "
        b"and add `type: custom:fuelwatch-wa-card` to your dashboard.\n\n"
        b"Use only one version of the card resource at a time. "
        b"OpenStreetMap tiles are fetched when the map is displayed.\n"
    )
    write_archive(output / "FuelWatch-WA-Card.zip", card)


if __name__ == "__main__":
    main()
