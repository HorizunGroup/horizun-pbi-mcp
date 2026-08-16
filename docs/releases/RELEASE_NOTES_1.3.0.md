# Horizun PBI MCP v1.3.0

One new tool (128 total), zero breaking changes — and four defects that had
the same shape underneath: **the server knew something and acted as if it
didn't.**

Two of them were found writing a real 5D dashboard; two more reviewing the
fix for the first two. Every one is verified by mutation: revert the fix and a
named test fails.

## Added

- **Custom visuals in page writing** — third-party visuals by GUID.
  They are **discovered**, never hardcoded: the role contract comes from
  `<Report>/CustomVisuals/<GUID>/resources/<GUID>.pbiviz.json`, published by
  the visual itself, and is validated as strictly as a native one. Native
  contracts are deliberately **not** applied to third parties.
- **`pbi_reflow_pages`** — the way back after changing design system. Rescales
  every visual to the new canvas (clamping what no longer fits) and recomputes
  the text colour that was baked in at composition time. It does **not**
  recompose: guessing each visual's intent would be worse. `dry_run=true` by
  default.

## Fixed

### The two server states could diverge silently

`active_model` (Desktop's memory) and `active_pbip` (disk) were never
cross-checked. They can point at different clients' files while every response
looks normal, because each half is valid on its own. Real near-miss: four
pages almost written into another client's report, caught only because a
visual count didn't add up.

Now crossed at `assert_escritura_pbir`, the single PBIR write gate. **It
blocks on confirmed divergence and never on `unknown`** — refusing because you
couldn't verify would make the server useless on a locked-down machine.

### Live measures were ephemeral and said so in a footnote

Writing with `mode='live'` goes to Desktop's memory, not to disk. The warning
existed as a `note`, which is read last or not at all; five measures were lost
to a close-without-save and four cards broke. Live writes now return
`persisted: false` **and** a warning stating the consequence, and the envelope
escalates the whole response to `WARNING`.

### Changing the canvas left orphans off-screen

Composing in `sala` (1920×1080), switching to `informe` (1280×720) and
recomposing with `merge` kept the previous visuals **outside the canvas**:
invisible when you open the report, but present in the render and in
publication. `merge` now warns before the damage, with the count, the ids and
both ways out.

Related, and undocumented until now: the text colour of decorative elements
does come from the theme, but it is baked in **at composition time** and
written as a literal. A title composed on a dark theme stays white-on-white
after switching to light. Nothing fails; it just can't be read.
`pbi_reflow_pages` recomputes it.

### Idempotency could authorise the same mutation twice

Four separate ways in, all closed:

1. **The reservation was not atomic.** Read → decide → write, with no mutual
   exclusion: two concurrent calls with the same `request_id` both saw no
   record and both executed. Now a per-`request_id` lock — `threading.Lock`
   between threads, `fcntl.flock`/`msvcrt.locking` between processes — plus
   `O_CREAT|O_EXCL` on the create, which survives a filesystem that ignores
   locks.
2. **A stale `in_flight` was reclaimed on age alone.** "Old" does not prove
   "dead": `pbi_open_and_refresh` on a large model passes the threshold while
   perfectly alive, and the first attempt's `terminar_ok` then overwrote the
   second's record. No automatic reclaim any more: `request_outcome_unknown`,
   `safe_to_retry=false`, and an explicit recovery. Each authorisation now
   mints a non-reusable `attempt_id`; closing is a compare-and-set under the
   same exclusion, so an old attempt can never complete over a newer one.
3. **`safe_to_retry` was stored and never consulted.** A
   `bulk_partially_applied` closed as unsafe re-executed on the next call.
   The verdict now governs authorisation.
4. **The TTL deleted uncertain state.** Records older than 24 h were hidden
   and purged, `in_flight` included — "expired" became "never happened" and
   the request was authorised again. Uncertainty does not expire: only
   unambiguously safe terminal states are purged, each decided under its own
   lock.

### Corrupt JSON is never overwritten

An unreadable idempotency record was treated as absent, so the next call
overwrote it: it enabled a mutation that may already have run **and** destroyed
the only evidence of it. `session.json` had the same hole — the parse error was
swallowed and the first `_persist()` replaced the file. Both now fail closed,
preserve the file byte for byte, and say so with a recovery path.
Nothing is renamed, moved or deleted automatically; a person decides that.

An `OSError` while reading no longer means "absent" either: a disk failure was
being converted into permission to mutate.

## Notes

- The frozen contract of the original 34 tools is untouched, and so are the
  signatures, defaults and response shapes of the other 94.
- `pbi_session_info` gains `persisted_session`; additive.
- Python 3.10 remains the minimum, now guarded by a test that catches
  3.12-only syntax locally instead of in CI.
