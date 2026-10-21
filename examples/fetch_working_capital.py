"""Download SEC snapshots separately from the offline examples and tests."""

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sys
import time
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd

from mini_tms import config as cfg
from mini_tms.config import C
from mini_tms.working_capital import annual_companyfacts, chart_ccc, working_capital_table

# CONFIG: edit SEC_USER_AGENT in mini_tms/config.py to provide a working contact.
MANIFEST = cfg.DATA_DIRECTORY / "manifest.json"
RESEARCH_NOTE = "Annual 10-K USD facts; exact period alignment; latest filed fact per tag and period."


def fetch_working_capital():
    """Preserve raw bytes and unsuccessful requests; never substitute synthetic issuer data."""
    cfg.DATA_DIRECTORY.mkdir(parents=True, exist_ok=True)
    manifest = {"method": RESEARCH_NOTE, "requests": [], "issuers": []}
    last_request = 0.0

    def save_manifest():
        MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    def fetch(url, filename):
        nonlocal last_request
        wait = cfg.SEC_INTERVAL_SECONDS - (time.monotonic() - last_request)
        if wait > 0:
            time.sleep(wait)
        entry = {"url": url, "retrieved_at": datetime.now(timezone.utc).isoformat(),
                 "path": filename, "sha256": None, "status": "failed"}
        manifest["requests"].append(entry)
        try:
            last_request = time.monotonic()
            request = Request(url, headers={"User-Agent": cfg.SEC_USER_AGENT, "Accept": "application/json"})
            with urlopen(request, timeout=cfg.SEC_TIMEOUT_SECONDS) as response:
                raw = response.read()
            (cfg.DATA_DIRECTORY / filename).write_bytes(raw)
            entry.update(sha256=sha256(raw).hexdigest(), status="ok")
            return json.loads(raw)
        except Exception as error:
            entry["error"] = f"{type(error).__name__}: {error}"
            raise
        finally:
            save_manifest()

    try:
        tickers = fetch(cfg.TICKERS_URL, "company_tickers.json")
    except Exception:
        print(json.dumps(manifest, indent=2))
        return False
    frames = []
    lookup = {r["ticker"]: int(r["cik_str"]) for r in tickers.values()}
    for issuer in cfg.WC_ISSUERS:
        entry = {C.ISSUER: issuer, "status": "failed"}
        manifest["issuers"].append(entry)
        try:
            cik = lookup[issuer]
            payload = fetch(cfg.FACTS_URL.format(cik=cik), f"CIK{cik:010d}.json")
            frame = annual_companyfacts(payload, issuer)
            frames.append(frame)
            entry.update(status="ok", years=len(frame))
        except Exception as error:
            entry["error"] = f"{type(error).__name__}: {error}"
        save_manifest()
    if len(frames) == len(cfg.WC_ISSUERS):
        snapshot = pd.concat(frames, ignore_index=True)
        snapshot.to_csv(cfg.SNAPSHOT, index=False)
        manifest["snapshot"] = {"path": cfg.SNAPSHOT.name, "sha256": sha256(cfg.SNAPSHOT.read_bytes()).hexdigest(),
                                "complete": True}
        table = working_capital_table(snapshot)
        table.to_csv(cfg.DATA_DIRECTORY / "ccc.csv", index=False)
        chart_ccc(table, cfg.DATA_DIRECTORY / "ccc.png")
    save_manifest()
    print(json.dumps(manifest, indent=2))
    return len(frames) == len(cfg.WC_ISSUERS)


if __name__ == "__main__":
    raise SystemExit(0 if fetch_working_capital() else 1)
