# Stored-check execution safety — 2026-08-29

## Decision under test

Running one stored check must cross the same visible, explicit execution
boundary as running the board's batch. Janus prints each effective command in
full through an unambiguous terminal-safe escaped representation, flushes the
preview, and then either receives interactive confirmation or requires `--yes`
from an unattended caller. Quotes delimit the command and escapes distinguish
literal backslashes from terminal controls; the original stored string, not the
representation, executes after consent. Consent applies only to that command: a
revision visible at the final load fails closed, while a later commit cannot
substitute its unseen bytes and applies to the next run.

This is a security boundary around intentional same-user shell execution. It is
not shell sanitization, a sandbox, or authority to execute a gate written by a
lower-trust or remote principal. Remote writes remain deliberately unavailable.

## Live measurement

A read-only query of `~/.janus/janus.db` on mickey at 2026-08-29 found:

- 33 gates and 20 effective stored commands: 17 decay, 3 delivery;
- 2 append-only check revisions and 8 recorded observations;
- command lengths from 34 to 188 characters, median 126;
- 14 of 20 commands longer than a 110-column terminal; and
- no commands containing a newline.

No command bodies were copied into this artifact. The distribution makes
unclipped preview behavior load-bearing rather than cosmetic.

A disposable pre-change CLI probe raised a gate whose decay check created a
marker, then invoked `janus check <id>` with stdin disconnected. The command was
not displayed, the marker was created, an observation was appended, and the
process exited 0. That reproduces the defect without touching the live ledger.

## Behavior cases

The invariant suite covers these distinct cases:

1. unattended single-check without `--yes` shows the full command and refuses;
2. refusal creates neither a shell side effect nor an observation;
3. `--yes` skips only the prompt and still prints the command;
4. the newest append-only revision is both previewed and executed;
5. the superseded original command is neither shown nor run;
6. the preview occurs before the call into the execution function;
7. an interactive decline records and executes nothing;
8. decay and delivery kinds use the same boundary;
9. a revision visible at the final command load invalidates consent;
10. a revision committed after that load cannot replace the displayed bytes;
11. stdout is flushed before the execution function is called;
12. the board passes each previewed command into the same identity guard; and
13. the board's existing unattended refusal and full-preview behavior remains
    unchanged through the shared implementation;
14. a carriage return capable of repainting the prompt is rendered as inert
    `\\r`, while the exact stored command still executes and is recorded.

## Assumptions and non-goals

- The repository threat model and the current same-OS-user deployment are the
  authoritative trust boundary for this slice.
- `--yes` means the caller has already chosen to run stored text; it does not
  make that text safe and does not suppress its display.
- This change adds no timer, remote write, privilege boundary, schema change,
  or new dependency.
- The live command set has no newlines, but that measurement is not a security
  assumption: all ASCII controls and non-ASCII code points are escaped before
  display. Lower-trust writers remain out of scope by design.

## Verification evidence

- Before implementation, `python -m pytest -q tests/test_invariants.py -k
  'single_check'` failed all four initial boundary tests: the unattended path
  exited 0 and created its marker, `--yes` was unknown, and neither preview nor
  decline existed.
- With the exact-command comparison deliberately removed, `python -m pytest -q
  tests/test_invariants.py -k 'command_changes_after_preview'` failed because
  no error was raised and the unseen replacement executed. Restoring the guard
  makes the test pass.
- With both the explicit stdout flush and the board's expected-command argument
  deliberately removed, their two focused regressions failed. Restoring both
  makes the tests pass.
- A late independent review supplied a real-terminal carriage-return repaint
  reproducer. The raw-byte regression failed against the reviewed candidate
  because `\\r` reached stdout; changing the shared preview to Python's
  reversible ASCII representation made it pass without changing the executed
  or recorded command.
- `./scripts/check.sh` compiles the package and passes 106 tests: all 77
  invariant tests plus the 29 stable-export tests now present on main.
- A clean Python 3.12 virtual environment installed the candidate as a regular
  site-packages copy (not editable). Its unattended call exited 2, printed the
  complete command and `--yes` recovery, and left zero observations and no
  marker. The `--yes` call printed the same command, created the marker, and
  recorded exactly one observation containing the displayed bytes.
- `git diff --check` is clean. A repository scan found no credential-shaped
  strings. No dependency was added, so there is no changed dependency surface
  to audit.
