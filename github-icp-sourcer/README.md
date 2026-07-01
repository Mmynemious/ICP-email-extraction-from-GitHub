# github-icp-sourcer

Finds real people on GitHub who fit a given ICP (ideal customer profile) and
have a **publicly discoverable email address**, so you can do honest,
low-volume, manually-reviewed cold outreach — the kind that gets replies
instead of spam complaints.

This is the exact mechanism behind Theioptera's early-lead sourcing: instead
of buying a scraped list, it searches GitHub for people *actively building or
learning in your niche* (via their public repos, bios, and stars), then only
keeps the ones who've listed a real, public email address.

## How it works

```
1. SEARCH        →  GitHub code/repo search: subfield keyword × learner-signal
                     phrase (e.g. "scanpy" + "tutorial", "DESeq2" + "beginner")
2. DEDUPE        →  Skip logins you've already sourced (local file, or your
                     Notion CRM via --notion-dedupe)
3. FIND EMAIL    →  Profile email field → else public commit author email.
                     No email found = dropped. Public data only.
4. FILTER        →  Auto-skip Organizations, "Lab"/"Group" accounts, arxiv-
                     digest bots, awesome-list curators, mega-starred repos
5. OUTPUT        →  leads.tsv: handle, name, email, profile URL, the exact
                     signal (repo/query) that surfaced them
```

You still do the outreach yourself — this tool only gets you to a reviewed
list of real people. See "Ethics & ground rules" below.

## Quickstart

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # then fill in GITHUB_TOKEN
cp config/queries.example.yaml config/queries.yaml   # then edit for your ICP

python sourcer.py --config config/queries.yaml --target 50
```

`GITHUB_TOKEN` just needs to be *any* personal access token — no special
scopes required, since everything queried here is public. Create one at
https://github.com/settings/tokens.

Output lands in `leads.tsv`:

```
handle    name         email                profile                          signal
janedoe   Jane Doe     jane@example.edu     https://github.com/janedoe       tool_repos: scrna-tutorial [...]
```

## Adapting this to a different ICP

The whole tool is driven by `config/queries.yaml`. Nothing in `sourcer.py`
is specific to bioinformatics — that's just the worked example in
`config/queries.example.yaml`. To retarget it:

1. **Pick 4-8 tools/keywords** people in your niche actually use
   (e.g. for indie game devs: `"godot"`, `"unity"`, `"pixel art"`).
2. **Pick 4-8 learner-signal phrases**: `"tutorial"`, `"learning"`,
   `"beginner"`, `"journey"`, `"notes"`, `"self-study"`, `"portfolio"`,
   `"100 days"`.
3. Fill `categories.tool_repos` with combinations of the two — this is
   where most of your leads will come from.
4. Fill `user_bio_queries` with phrases people in your niche put in their
   GitHub bio (`"aspiring game dev"`, `"learning gamedev"`).
5. Fill `stargazer_repos` with 3-6 well-known "getting started" repos in
   your field — people who starred them are near-certainly learners there.
6. Run it, look at the output, and **rotate the queries every round**. The
   same query set mined twice mostly returns people you already have.

## Filtering — and its limits

`sourcer.py` auto-excludes:
- GitHub Organization accounts (unless `--include-orgs`)
- Logins/repo owners matching `config/exclude_patterns.txt` (arxiv-digest
  bots, awesome-list curators, `*-lab`/`*-labs` accounts by default)
- Profiles whose bio reads like a PI/professor/lab head
- Repos with `>= --max-stars` stars (default 200) — already-famous, not a
  cold lead

**It will not catch everything.** In practice, expect to eyeball the output
and manually drop:
- Established tool authors / senior researchers the bio-check missed
- Fork-farm accounts (same display name, many logins, different throwaway
  emails — a sign of an automated or shared setup)
- Off-topic false positives from loose `OR` queries (avoid `OR` in your
  queries where possible; it drags in unrelated repos)

Add anything recurring to `config/exclude_patterns.txt` so future rounds
skip it automatically.

## Deduping against a CRM

If you already track contacted people in Notion:

```bash
python sourcer.py --config config/queries.yaml --notion-dedupe
```

with `NOTION_TOKEN` and `NOTION_DATA_SOURCE_ID` set in `.env`. It pulls every
GitHub URL already in that data source and skips those logins. Otherwise,
pass `--known-logins path/to/logins.txt` (one login per line) for a simple
local dedupe list — append newly-found logins to it after each run.

## Ethics & ground rules

- **Public data only.** This surfaces a GitHub profile's public email field,
  or the author email on a public commit they made themselves. It does not
  scrape private data, guess emails, or use any data broker.
- **Respect GitHub's API terms and rate limits.** The client sleeps on 403s;
  don't remove that or hammer the search endpoints.
- **Drafts, not sends.** Generate outreach as drafts you personally review
  before sending — never wire this into an auto-send pipeline. Every person
  in `leads.tsv` is a real individual, not a number.
- **One clear ask, easy to ignore.** Keep outreach short, specific to the
  repo/signal that surfaced them, and easy to not respond to.
- **Know your local email law** before sending unsolicited outreach at any
  volume (e.g. CAN-SPAM in the US, GDPR/national UWG rules in the EU).

## Using the Claude Code skill

This repo ships a Claude Code skill at
`.claude/skills/source-github-leads/SKILL.md`. If you're working in Claude
Code with this repo open, invoking it (or just asking Claude to "source
leads for X ICP") walks through building the query bank, running the tool,
and applying the manual-review filters above — the same workflow used to
validate this against a real product.
