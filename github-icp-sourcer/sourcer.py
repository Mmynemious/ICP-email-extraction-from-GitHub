#!/usr/bin/env python3
"""
github-icp-sourcer
-------------------
Finds real people on GitHub who fit a given ICP (ideal customer profile) and
have a publicly discoverable email address, so you can do honest, low-volume,
manually-reviewed cold outreach.

Public data only: GitHub profile email field, or (as fallback) the author
email on their own public commits. Nothing private, nothing scraped from
behind auth. Drafts-only outreach is a separate step you do yourself.

Usage:
    python sourcer.py --config config/queries.yaml --target 50

See README.md for the full walkthrough and config/queries.example.yaml for
the query format.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
import yaml

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

DEFAULT_EXCLUDE_PATTERNS = [
    r"(?i)^awesome[-_]",
    r"(?i)daily.?arxiv",
    r"(?i)arxiv.?daily",
    r"(?i)paper.?a.?day",
    r"(?i)-?lab$",
    r"(?i)^lab-",
    r"(?i)-labs?$",
]


# ── .env loading (no external dependency) ──────────────────────────────────
def load_dotenv(path: str = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


# ── config ───────────────────────────────────────────────────────────────
def load_yaml(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_lines(path: str | None) -> set[str]:
    if not path or not Path(path).exists():
        return set()
    return {
        line.strip().lower()
        for line in Path(path).read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    }


# ── email helpers ────────────────────────────────────────────────────────
def is_real_email(email: str | None) -> bool:
    if not email:
        return False
    email = email.strip().lower()
    if not EMAIL_RE.match(email):
        return False
    if "noreply" in email or "users.noreply.github.com" in email:
        return False
    if email.endswith("@github.com") or "actions@github" in email:
        return False
    return True


class GitHubClient:
    def __init__(self, token: str):
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def get_profile_email(self, login: str) -> tuple[str | None, str, str, int]:
        """Returns (email_or_None, display_name, owner_type, bio_text)."""
        r = requests.get(f"https://api.github.com/users/{login}", headers=self.headers, timeout=8)
        if r.status_code != 200:
            return None, login, "User", ""
        u = r.json()
        email = u.get("email")
        name = u.get("name") or login
        owner_type = u.get("type", "User")
        bio = (u.get("bio") or "")
        return (email if is_real_email(email) else None), name, owner_type, bio

    def commit_email(self, login: str) -> str | None:
        """Check first few own non-fork repos for a public commit author email."""
        r = requests.get(
            f"https://api.github.com/users/{login}/repos",
            headers=self.headers,
            params={"sort": "pushed", "per_page": 6},
            timeout=8,
        )
        if r.status_code != 200:
            return None
        for repo in r.json():
            if repo.get("fork"):
                continue
            cr = requests.get(
                f"https://api.github.com/repos/{login}/{repo['name']}/commits",
                headers=self.headers,
                params={"author": login, "per_page": 3},
                timeout=8,
            )
            if cr.status_code != 200:
                continue
            for commit in cr.json():
                try:
                    e = commit["commit"]["author"]["email"]
                    if is_real_email(e):
                        return e
                except (KeyError, TypeError):
                    pass
        return None

    def search_repos(self, query: str, per_page: int = 15) -> list[dict]:
        r = requests.get(
            "https://api.github.com/search/repositories",
            headers=self.headers,
            params={"q": f"{query} fork:false", "sort": "updated", "order": "desc", "per_page": per_page},
            timeout=12,
        )
        if r.status_code == 403:
            print("  [rate-limit] sleeping 15s ...", file=sys.stderr)
            time.sleep(15)
            return []
        if r.status_code != 200:
            return []
        return r.json().get("items", [])

    def search_users(self, query: str, per_page: int = 15) -> list[dict]:
        r = requests.get(
            "https://api.github.com/search/users",
            headers=self.headers,
            params={"q": query, "sort": "joined", "order": "desc", "per_page": per_page},
            timeout=12,
        )
        if r.status_code == 403:
            print("  [rate-limit] sleeping 15s ...", file=sys.stderr)
            time.sleep(15)
            return []
        if r.status_code != 200:
            return []
        return r.json().get("items", [])

    def stargazers_of(self, owner_repo: str, max_pages: int = 2) -> list[str]:
        logins: list[str] = []
        for page in range(1, max_pages + 1):
            r = requests.get(
                f"https://api.github.com/repos/{owner_repo}/stargazers",
                headers=self.headers,
                params={"per_page": 30, "page": page},
                timeout=12,
            )
            if r.status_code != 200:
                break
            batch = r.json()
            if not batch:
                break
            logins.extend(u["login"] for u in batch if "login" in u)
            time.sleep(0.5)
        return logins


# ── optional: dedupe against a Notion CRM data source ───────────────────
def fetch_known_logins_from_notion(token: str, data_source_id: str) -> set[str]:
    known: set[str] = set()
    url = f"https://api.notion.com/v1/data_sources/{data_source_id}/query"
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2025-09-03",
        "Content-Type": "application/json",
    }
    cursor = None
    while True:
        body: dict = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        r = requests.post(url, headers=headers, json=body, timeout=20)
        if r.status_code != 200:
            print(f"[warn] Notion query failed: {r.status_code}", file=sys.stderr)
            break
        data = r.json()
        for page in data.get("results", []):
            for v in page.get("properties", {}).values():
                if v.get("type") == "url" and v.get("url") and "github.com/" in (v.get("url") or ""):
                    try:
                        path = urlparse(v["url"]).path.strip("/").split("/")[0].lower()
                        if path:
                            known.add(path)
                    except Exception:
                        pass
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return known


# ── filtering heuristics ─────────────────────────────────────────────────
PI_BIO_RE = re.compile(
    r"(?i)\b(professor|principal investigator|\bpi\b at|lab head|group leader|"
    r"associate professor|assistant professor|chair of|department head)\b"
)


def matches_exclude_patterns(login: str, patterns: list[str]) -> bool:
    return any(re.search(p, login) for p in patterns)


def is_org_or_lab(owner_type: str, name: str) -> bool:
    if owner_type == "Organization":
        return True
    return bool(re.search(r"(?i)\b(lab|labs|group|institute)\b", name or ""))


# ── main sourcing loop ───────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config/queries.yaml", help="Path to your ICP query bank (YAML)")
    ap.add_argument("--target", type=int, default=50, help="Stop once this many leads with emails are found")
    ap.add_argument("--output", default="leads.tsv", help="Output TSV path")
    ap.add_argument("--known-logins", default=None, help="Newline-delimited file of GitHub logins to skip")
    ap.add_argument("--exclude-patterns", default="config/exclude_patterns.txt",
                     help="Extra regex patterns (one per line) for logins to auto-skip")
    ap.add_argument("--max-stars", type=int, default=200,
                     help="Skip repos with >= this many stars (already-famous, not a cold lead)")
    ap.add_argument("--include-orgs", action="store_true", help="Don't auto-skip Organization-owned repos")
    ap.add_argument("--notion-dedupe", action="store_true",
                     help="Also pull known GitHub logins from a Notion CRM data source (needs NOTION_TOKEN + NOTION_DATA_SOURCE_ID in .env)")
    args = ap.parse_args()

    load_dotenv()
    github_token = os.environ.get("GITHUB_TOKEN")
    if not github_token:
        sys.exit("Missing GITHUB_TOKEN. Copy .env.example to .env and fill it in.")

    gh = GitHubClient(github_token)
    cfg = load_yaml(args.config)

    exclude_patterns = list(DEFAULT_EXCLUDE_PATTERNS)
    if Path(args.exclude_patterns).exists():
        exclude_patterns += [
            line.strip() for line in Path(args.exclude_patterns).read_text().splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]

    seen = load_lines(args.known_logins)
    if args.notion_dedupe:
        notion_token = os.environ.get("NOTION_TOKEN")
        ds_id = os.environ.get("NOTION_DATA_SOURCE_ID")
        if notion_token and ds_id:
            notion_known = fetch_known_logins_from_notion(notion_token, ds_id)
            print(f"[info] {len(notion_known)} known logins pulled from Notion CRM", file=sys.stderr)
            seen |= notion_known
        else:
            print("[warn] --notion-dedupe set but NOTION_TOKEN/NOTION_DATA_SOURCE_ID missing in .env", file=sys.stderr)

    results: list[dict] = []

    def try_add(login: str, signal: str, profile_url: str) -> bool:
        if login.lower() in seen or matches_exclude_patterns(login, exclude_patterns):
            return False
        seen.add(login.lower())
        print(f"  checking {login} ...", file=sys.stderr)
        email, name, owner_type, bio = gh.get_profile_email(login)
        if not args.include_orgs and owner_type == "Organization":
            return False
        if PI_BIO_RE.search(bio):
            return False
        time.sleep(0.25)
        if not email:
            email = gh.commit_email(login)
            time.sleep(0.25)
        if email:
            results.append({"handle": login, "name": name, "email": email, "profile": profile_url, "signal": signal})
            print(f"  ✓ {login} -> {email}", file=sys.stderr)
            return True
        return False

    def repo_owner_ok(repo: dict) -> bool:
        if repo.get("stargazers_count", 0) >= args.max_stars:
            return False
        owner = repo.get("owner", {})
        if not args.include_orgs and owner.get("type") == "Organization":
            return False
        if is_org_or_lab(owner.get("type", ""), owner.get("login", "")):
            return False
        return True

    categories = cfg.get("categories", {})

    for cat_name, queries in categories.items():
        if len(results) >= args.target:
            break
        print(f"\n[phase] {cat_name} ...", file=sys.stderr)
        for q in queries:
            if len(results) >= args.target:
                break
            for repo in gh.search_repos(q, per_page=12):
                if len(results) >= args.target:
                    break
                if not repo_owner_ok(repo):
                    continue
                owner = repo.get("owner", {})
                login = owner.get("login", "")
                if not login:
                    continue
                try_add(login, f"{cat_name}: {repo['name']} [{q[:40]}]", owner.get("html_url", ""))
            time.sleep(1)

    for q in cfg.get("user_bio_queries", []):
        if len(results) >= args.target:
            break
        for user in gh.search_users(q, per_page=15):
            if len(results) >= args.target:
                break
            login = user.get("login", "")
            if not login:
                continue
            try_add(login, f"bio: {q[:50]}", user.get("html_url", ""))
        time.sleep(1)

    for owner_repo in cfg.get("stargazer_repos", []):
        if len(results) >= args.target:
            break
        print(f"  -> stargazers of {owner_repo}", file=sys.stderr)
        for login in gh.stargazers_of(owner_repo):
            if len(results) >= args.target:
                break
            try_add(login, f"starred: {owner_repo}", f"https://github.com/{login}")
        time.sleep(1)

    print(f"\n{'=' * 70}", file=sys.stderr)
    print(f"FOUND {len(results)} leads with public emails", file=sys.stderr)
    print(f"{'=' * 70}\n", file=sys.stderr)

    with open(args.output, "w", encoding="utf-8") as f:
        f.write("handle\tname\temail\tprofile\tsignal\n")
        for r in results:
            f.write(f"{r['handle']}\t{r['name']}\t{r['email']}\t{r['profile']}\t{r['signal']}\n")

    print(f"Wrote {len(results)} leads to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
