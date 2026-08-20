# nse-filing-watcher

A tiny, free-to-run watcher: every 5 minutes (during market hours, IST), it
checks NSE's public corporate-announcements feed for anything filed today
that looks like an earnings-call transcript, and — if it finds one newer
than the last check — fires a [`repository_dispatch`][dispatch] webhook at a
downstream GitHub repo.

That's the whole thing. It doesn't parse PDFs, doesn't know which company
matters, doesn't try to be the source of truth about anything. It exists
because polling frequently is free on a public repo (GitHub Actions minutes
are unlimited on standard runners for public repos) but not on a private
one, so this repo does the frequent, cheap "did anything change" check, and
lets a private downstream pipeline do the expensive, precise part only when
there's actually something to look at.

## Why this design

A downstream pipeline almost always already has to dedupe against its own
published state anyway (a filing can be seen more than once, a run can be
retried, etc.), so this watcher doesn't need to be precise — it only needs
to answer "did anything change since last time," and a false positive here
just costs the downstream system one cheap no-op check. That's what keeps
this repo small: no real state beyond "when did I last see something new."

[dispatch]: https://docs.github.com/en/rest/repos/repos#create-a-repository-dispatch-event

## Setup

1. Fork or clone this repo.
2. In the downstream (private) repo, create a workflow trigger:
   ```yaml
   on:
     repository_dispatch:
       types: [new-filing-detected]
   ```
3. Create a fine-grained Personal Access Token scoped to **only** the
   downstream repo, with **Actions: write** permission (nothing broader —
   this token lives as a secret in a public repo; keep its blast radius to
   exactly what it needs).
4. In this repo's settings, add two secrets:
   - `TARGET_REPO` — `owner/repo` of the downstream repository.
   - `DISPATCH_TOKEN` — the token from step 3.
5. Done. The workflow runs on its own schedule; no further setup needed.

## Adjusting what counts as "found something"

`TRANSCRIPT_PATTERN` in `check_and_dispatch.py` is the only filter. It's
deliberately broad (a false positive is cheap; a missed filing is not) — a
`transcript`, `earnings call`, `conference call`, `con call`, `analyst call`,
or `investor call` mention in the announcement's subject or attachment text
is enough to trigger a dispatch.

## License

MIT — see [LICENSE](LICENSE).
