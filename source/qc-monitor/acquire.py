"""Save dated KNMI responses without overwriting previous snapshots."""

import argparse
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from records import ROOT, code_identity, digest, save_json, settings, utc_now

DOCUMENTS = {
    "script-access": "https://www.knmi.nl/kennis-en-datacentrum/achtergrond/data-ophalen-vanuit-een-script",
    "hourly-archive": "https://www.knmi.nl/nederland-nu/klimatologie/uurgegevens",
    "field-selection": "https://www.daggegevens.knmi.nl/klimatologie/uurgegevens",
    "copyright": "https://www.knmi.nl/copyright",
}


def early_parameters(year):
    if year not in (2021, 2022, 2023):
        raise ValueError("Only 2021-2023 acquisition is implemented; final intake is locked.")
    return {"stns": "240:260", "vars": "T:U", "start": f"{year}010101",
            "end": f"{year}123124"}


def download(url, params, directory, name):
    directory.mkdir(parents=True, exist_ok=False)
    body = urllib.parse.urlencode(params).encode("ascii") if params else None
    request = urllib.request.Request(url, data=body, headers={
        "User-Agent": "Dissertation-QC-data-foundation/0.1 (academic research)",
        "Accept-Encoding": "identity",
    })
    record = {"started_utc": utc_now(), "url": url, "parameters": params,
              "method": request.get_method(), "code": code_identity()}
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = response.read()
            record.update(status=response.status, resolved_url=response.url,
                          headers={k: v for k, v in response.headers.items()
                                   if k.lower() in ("content-type", "content-length", "date",
                                                    "etag", "last-modified", "content-encoding")})
        raw = directory / name
        with raw.open("xb") as stream:
            stream.write(payload)
        record.update(finished_utc=utc_now(), bytes=len(payload), sha256=digest(payload),
                      raw_path=str(raw.relative_to(ROOT)), outcome="downloaded_not_yet_accepted")
    except Exception as error:
        record.update(finished_utc=utc_now(), outcome="failed", error=repr(error))
        save_json(directory / "manifest.json", record)
        raise
    save_json(directory / "manifest.json", record)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=["documentation", "early"])
    parser.add_argument("--year", type=int, choices=[2021, 2022, 2023])
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    if args.kind == "documentation":
        for name, url in DOCUMENTS.items():
            result = download(url, None, ROOT / "data" / "source-docs" / stamp / name,
                              "response.html")
            print(json.dumps({k: result[k] for k in ("raw_path", "bytes", "sha256")}))
    else:
        if args.year is None:
            parser.error("early requires --year")
        result = download(settings()["source_url"], early_parameters(args.year),
                          ROOT / "data" / "raw" / f"knmi-{args.year}-{stamp}", "response.txt")
        print(json.dumps({k: result[k] for k in ("raw_path", "bytes", "sha256")}))


if __name__ == "__main__":
    main()
