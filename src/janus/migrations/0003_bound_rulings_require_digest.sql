-- Janus 0003 — a bound ruling cannot bind nothing.
--
-- `gates.binding_*` is optional because the M0 corpus admitted gates with no
-- artifact. Once a gate DOES carry a binding, however, invariant 2 is strict:
-- an approved or refused ruling records the SHA-256 observed at ruling time.
--
-- Before this trigger, an artifact that disappeared between raise and decide
-- made `digest_of_live` return NULL. SQLite accepted the ruling, permanently
-- closing the gate with no bytes anyone could re-check. The CLI now refuses
-- that path with a useful sentence; this trigger makes the same invariant hold
-- for every writer, not only callers that used the CLI correctly.
--
-- Existing invalid rows remain readable. Append-only means a migration may not
-- rewrite or delete them; `show` and `doctor` surface them so they cannot read
-- as fine.

CREATE TRIGGER ruling_bound_gate_requires_digest BEFORE INSERT ON rulings
WHEN NEW.state IN ('approved', 'refused')
     AND NEW.bound_sha256 IS NULL
     AND EXISTS (
         SELECT 1 FROM gates g
         WHERE g.id = NEW.gate_id AND g.binding_sha256 IS NOT NULL
     )
BEGIN
    SELECT RAISE(ABORT,
        'janus: a bound ruling must record the digest observed at ruling time');
END;
