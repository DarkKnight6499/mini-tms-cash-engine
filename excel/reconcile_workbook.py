"""Recalculate with LibreOffice when installed, then compare every formula to Python."""

import json
from math import isfinite
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from openpyxl import load_workbook

from mini_tms import config as cfg

# CONFIG: all recalculation files stay inside the checkout.
WORKBOOK_PATH = cfg.WORKBOOK
OUTPUT_DIRECTORY = cfg.RECALC_DIRECTORY
ABSOLUTE_TOLERANCE = 1e-6
REPORT_PATH = cfg.OUTPUT / "workbook_reconciliation.json"


def libreoffice_path():
    for candidate in cfg.LIBREOFFICE_CANDIDATES:
        found = shutil.which(candidate)
        if found:
            return found
        if Path(candidate).is_file():
            return candidate
    return None


def reconcile(path=WORKBOOK_PATH, output_directory=OUTPUT_DIRECTORY):
    engine = libreoffice_path()
    if engine is None:
        return {"status": "skipped", "reason": "LibreOffice absent; native formula values not recalculated"}
    output_directory.mkdir(parents=True, exist_ok=True)
    target = output_directory / path.name
    if target.resolve() == path.resolve():
        raise ValueError("Recalculation output directory must differ from workbook directory")
    target.unlink(missing_ok=True)
    profile = (output_directory / "profile").resolve().as_uri()
    result = subprocess.run([engine, f"-env:UserInstallation={profile}", "--headless", "--convert-to", "xlsx",
                             "--outdir", str(output_directory), str(path.resolve())],
                            capture_output=True, text=True, timeout=cfg.RECALC_TIMEOUT)
    if result.returncode or not target.exists():
        raise RuntimeError(f"LibreOffice conversion failed: {result.stderr or result.stdout}")
    expected = json.loads(path.with_name("reconciliation_expected.json").read_text(encoding="utf-8"))
    book = load_workbook(target, data_only=True)
    differences = {}
    for address, value in expected.items():
        name, cell = address.split("!")
        actual = book[name][cell].value
        if isinstance(value, str):
            matches = actual == value
        else:
            matches = (isinstance(actual, (float, int)) and isfinite(actual)
                       and isfinite(value) and abs(actual - value) <= ABSOLUTE_TOLERANCE)
        if not matches:
            differences[address] = {"python": value, "excel": actual}
    book.close()
    if differences:
        raise AssertionError(json.dumps(differences, indent=2))
    # Preserve the engine's cached results for spreadsheet viewers.
    shutil.copy2(target, path)
    return {"status": "passed", "formula_cells_compared": len(expected), "engine": engine}


if __name__ == "__main__":
    report = reconcile()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
