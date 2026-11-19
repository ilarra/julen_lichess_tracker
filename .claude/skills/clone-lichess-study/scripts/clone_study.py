#!/usr/bin/env python3
"""Copy a Lichess study into the account that owns the token.

Lichess has no "clone" API, so this rebuilds the study: it downloads the original's
chapters as PGN, creates a new study and imports them one by one (keeping each chapter's
name, starting position, moves, comments and board orientation), then removes the empty
chapter Lichess adds to every new study.

Optionally sends the new study's link to someone (the teacher) as a Lichess private message;
that needs the msg:write scope too. Lichess's API can't add study members, so a message with the
link is how the copy is shared (copies are unlisted: anyone with the link can open them).

Needs a personal token with the study:read and study:write scopes, in LICHESS_TOKEN.
Standard library only, so it runs anywhere with Python 3.9+.

  python clone_study.py https://lichess.org/study/VXfQjo3L
  python clone_study.py VXfQjo3L --name "Klase6 - julen" --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

API = "https://lichess.org"
STUDY_RE = re.compile(r"(?:lichess\.org/study/)?([A-Za-z0-9]{8})(?:/[A-Za-z0-9]{8})?/?$")
TAG_RE = re.compile(r'^\[(\w+) "((?:[^"\\]|\\.)*)"\]\s*$', re.M)
# tags that describe the original study; Lichess sets its own on import
DROP_TAGS = {"Event", "StudyName", "ChapterName", "ChapterURL", "Annotator", "UTCDate", "UTCTime"}


class LichessError(RuntimeError):
    pass


def request(method: str, path: str, token: str, data: dict | None = None, accept: str = "application/json"):
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(API + path, data=body, method=method, headers={
        "Authorization": f"Bearer {token}", "Accept": accept, "User-Agent": "study-cloner"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                text = r.read().decode("utf-8")
                return json.loads(text) if text and accept == "application/json" else text
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            if e.code == 429 and attempt < 2:
                print("Rate limited by Lichess, waiting 60 s…", file=sys.stderr)
                time.sleep(60)
                continue
            if e.code in (401, 403):
                raise LichessError(f"Lichess refused the token ({e.code}). It needs the study:read and "
                                   f"study:write scopes. {detail}") from None
            if e.code == 404:
                raise LichessError(f"Not found: {path}. The study may be private and not shared with this account.") from None
            raise LichessError(f"{method} {path} failed with {e.code}: {detail}") from None
    raise LichessError(f"{method} {path}: still rate limited")


def study_id(text: str) -> str:
    m = STUDY_RE.search(text.strip())
    if not m:
        raise LichessError(f"Not a Lichess study link or id: {text!r}")
    return m.group(1)


def split_chapters(pgn: str) -> list[dict]:
    """Lichess exports one PGN game per chapter, each starting with its tag section."""
    chunks = [c.strip() for c in re.split(r"\n\s*\n(?=\[)", pgn.strip()) if c.strip()]
    chapters = []
    for chunk in chunks:
        tags = dict(TAG_RE.findall(chunk))
        if not tags:
            continue
        movetext = TAG_RE.sub("", chunk).strip()
        kept = "\n".join(f'[{k} "{v}"]' for k, v in TAG_RE.findall(chunk) if k not in DROP_TAGS)
        chapters.append({
            "name": (tags.get("ChapterName") or "Chapter")[:100],
            "orientation": tags.get("Orientation", "").lower(),
            "study_name": tags.get("StudyName", ""),
            "pgn": f"{kept}\n\n{movetext or '*'}\n",
        })
    return chapters


def notify(user: str, template: str, result: dict, token: str, log=print) -> bool:
    """Send the copy's link to `user`. Never fails the clone: a missing scope is only reported."""
    text = template.format(url=result["url"], name=result["name"], original_name=result["original_name"],
                           original_url=f"{API}/study/{result['original']}").strip()
    try:
        request("POST", f"/api/msg/{user}", token, {"text": text})
    except LichessError as e:
        if "refused the token" in str(e):
            log(f"Not sent to {user}: the token needs the msg:write scope to send messages.")
        else:
            log(f"Not sent to {user}: {e}")
        return False
    log(f"Sent the link to {user}")
    return True


def clone(source: str, token: str, name: str | None = None, suffix: str = "", visibility: str = "unlisted",
          force: bool = False, dry_run: bool = False, log=print) -> dict:
    sid = study_id(source)
    me = request("GET", "/api/account", token)["username"]
    pgn = request("GET", f"/api/study/{sid}.pgn?clocks=false&comments=true&variations=true&orientation=true",
                  token, accept="application/x-chess-pgn")
    chapters = split_chapters(pgn)
    if not chapters:
        raise LichessError("The study has no chapters (or isn't visible to this account).")
    original_name = chapters[0]["study_name"] or sid
    suffix = suffix if suffix else f" - {me}"
    target = (name or f"{original_name}{suffix}")[:100]
    log(f"Original: {original_name} ({len(chapters)} chapters) → {me}'s new study: {target}")

    existing = request("GET", f"/api/study/by/{me}", token, accept="application/x-ndjson")
    existing = [json.loads(l) for l in existing.splitlines() if l.strip()]
    clash = [s for s in existing if s["name"].strip().casefold() == target.strip().casefold()]
    if clash and not force:
        raise LichessError(f'{me} already has a study called "{target}": {API}/study/{clash[0]["id"]} '
                           "(use --force to make another copy)")
    if len(chapters) > 64:
        raise LichessError(f"Lichess studies hold at most 64 chapters; this one has {len(chapters)}.")
    if dry_run:
        for i, ch in enumerate(chapters, 1):
            log(f"  {i:2}. {ch['name']}")
        return {"original": sid, "original_name": original_name, "name": target, "id": None, "url": None,
                "chapters": len(chapters)}

    new = request("POST", "/api/study", token, {
        "name": target, "visibility": visibility, "computer": "everyone", "explorer": "everyone",
        "cloneable": "everyone", "shareable": "everyone", "chat": "member", "sticky": "false",
    })
    new_id = new["id"]
    # the empty chapter Lichess creates with every study
    blank = re.findall(r'\[ChapterURL "[^"]*/study/\w{8}/(\w{8})"\]',
                       request("GET", f"/api/study/{new_id}.pgn", token, accept="application/x-chess-pgn"))
    for i, ch in enumerate(chapters):
        data = {"pgn": ch["pgn"], "name": ch["name"]}
        if ch["orientation"] in ("white", "black"):
            data["orientation"] = ch["orientation"]
        result = request("POST", f"/api/study/{new_id}/import-pgn", token, data)
        if result.get("error"):
            log(f"  warning, chapter {i + 1} ({ch['name']}): {result['error']}")
        if i == 0:
            for chapter_id in blank:   # delete after the first import: a study can't be left empty
                request("DELETE", f"/api/study/{new_id}/{chapter_id}", token, accept="*/*")
        time.sleep(0.3)
    url = f"{API}/study/{new_id}"
    log(f"Done: {url}")
    return {"original": sid, "original_name": original_name, "name": target, "id": new_id, "url": url,
            "chapters": len(chapters)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("study", help="study link or 8-character id")
    ap.add_argument("--name", help="name for the copy (default: original name + suffix)")
    ap.add_argument("--suffix", default=os.environ.get("CLONE_SUFFIX", ""),
                    help='appended to the original name (default: " - <username>")')
    ap.add_argument("--visibility", choices=["public", "unlisted", "private"], default="unlisted")
    ap.add_argument("--force", action="store_true", help="copy even if a study with that name exists")
    ap.add_argument("--dry-run", action="store_true", help="show what would be copied, change nothing")
    ap.add_argument("--notify", metavar="USER", help="send the copy's link to this Lichess user")
    ap.add_argument("--message", default="Hi! Here is my copy of {original_name}: {url}",
                    help="message text; {url} {name} {original_name} {original_url} are filled in "
                         "(or @path to read it from a file)")
    ap.add_argument("--record", help="append 'original_id copy_id date name' to this file")
    ap.add_argument("--summary", help="write a Markdown summary to this file (e.g. $GITHUB_STEP_SUMMARY)")
    args = ap.parse_args(argv)

    token = os.environ.get("LICHESS_TOKEN", "")
    if not token:
        sys.exit("Set LICHESS_TOKEN to a Lichess token with the study:read and study:write scopes.")
    try:
        result = clone(args.study, token, args.name or None, args.suffix, args.visibility, args.force, args.dry_run)
    except LichessError as e:
        if args.summary:
            with open(args.summary, "a") as f:
                f.write(f"### Clone failed\n\n{e}\n")
        sys.exit(f"Error: {e}")
    result["notified"] = None
    if args.notify and result["id"]:
        template = open(args.message[1:]).read() if args.message.startswith("@") else args.message
        result["notified"] = notify(args.notify, template, result, token)
    if args.record and result["id"]:
        with open(args.record, "a") as f:
            f.write(f"{result['original']} {result['id']} {date.today()} {result['name']}\n")
    if args.summary:
        with open(args.summary, "a") as f:
            f.write(f"### {'Would copy' if args.dry_run else 'Copied'} {result['original_name']}\n\n"
                    f"{result['chapters']} chapters → **{result['name']}**"
                    + (f"\n\n{result['url']}\n" if result["url"] else "\n")
                    + {True: f"\nLink sent to {args.notify}.\n",
                       False: f"\n**Link not sent to {args.notify}** (see log: token needs msg:write).\n"}.get(
                        result["notified"], ""))
    print(json.dumps(result))


if __name__ == "__main__":
    main()
