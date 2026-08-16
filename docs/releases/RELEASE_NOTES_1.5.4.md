# Horizun PBI MCP v1.5.4

Same tool surface as 1.5.3 (134 tools, contract untouched). One release, one
job: make installing on a **machine that has nothing** boring and reliable.

## The empty PC could still stall on `--scope user`

Every prerequisite was installed with `winget install --scope user`. That flag
only works if the package publishes an installer *tagged* as user-scope — and
when it doesn't, winget answers `No applicable installer found` (0x8A150044)
and stops, even though its default installer would have put everything in the
user profile anyway.

That tag lives in **someone else's manifest** and can change without notice, so
guessing which packages carry it is the wrong bet. Each install is now
attempted **both ways** — with the flag, then without — before giving up.

There is still no elevation anywhere: nothing requests administrator, and if an
install genuinely needed it, winget fails and the script reports it as pending
**with the exact package id** to hand to your IT department.

Verified with a stand-in `winget` that rejects the flag: the first call carries
`--scope user`, the second doesn't, and the install goes through. With a
`winget` that refuses everything, it reports the pending item instead of
claiming success.

## One command, no self-diagnosis required

The README used to open by asking readers to work out which case they were in.
Now it opens with a single PowerShell line that behaves correctly on a
fully-equipped machine and on a blank one:

```powershell
irm https://raw.githubusercontent.com/HorizunGroup/horizun-pbi-mcp/main/scripts/instalar.ps1 | iex
```

Restart Claude once and the tools are there. The in-chat prompt is still
offered — as a convenience for people already inside Claude Code, not as a
decision anyone has to make first.

The installer's closing message now also says that **the first launch will ask
you to sign in to Claude**. That is normal, and saying so out loud stops a
successful install from looking like a broken one.

## Verification

- Both winget branches exercised against stand-in binaries: scope rejected →
  recovers and installs; everything blocked → pending item with the package id,
  after four attempts, never a false success.
- The installer run end to end finishes `LISTO` with zero pending items.
- Full suite with packaging, contract check and `doctor.py` green; the one test
  that needs Power BI Desktop fully closed is environmental and covered by the
  clean-machine CI run.
