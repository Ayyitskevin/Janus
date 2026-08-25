-- Janus 0002 — a check can be corrected without anything being rewritten.
--
-- Why this exists. A decay or delivery check is executable text written ONCE, at
-- raise time, by someone guessing at a future they have not seen yet. The ledger
-- is append-only, so a wrong check could never be corrected — it misreported on
-- the board forever and no verb could touch it.
--
-- That stopped being theoretical on 2026-08-24. Gate g43344426bdb shipped the
-- delivery check `test -n "$ATHENA_API_TOKEN"`, which measures the ambient
-- environment of whatever process runs the board rather than any durable fact.
-- The token was delivered, mapped, and used to close nine issues — and the board
-- went on printing PROMISED, NOT DELIVERED. A board that lies once stops being
-- read, which is the failure the section was built to prevent, occurring inside
-- it.
--
-- The fix keeps append-only rather than bargaining with it: a correction is a NEW
-- ROW. Nothing is updated, nothing is deleted, the original check stays on the
-- gate and stays visible, and the revision records who replaced it and why. The
-- effective check is simply the newest revision, or the gate's original when
-- there is none.
--
-- What this deliberately is NOT: a way to make a gate say what you want. A
-- revision cannot change a gate's state, cannot alter a ruling, and cannot
-- rewrite what the previous check was. Someone could still revise a check to
-- `true` — and the mitigation is the same one `superseded` already relies on:
-- attribution plus append-only history, so a bad revision is visible,
-- answerable, and cheap to correct with another. Restriction would buy less than
-- it costs.

CREATE TABLE check_revisions (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    gate_id  TEXT NOT NULL REFERENCES gates(id),
    kind     TEXT NOT NULL CHECK (kind IN ('decay', 'delivery')),
    command  TEXT NOT NULL,
    at       TEXT NOT NULL,
    revised_by TEXT NOT NULL,
    -- Required, and required to be about MEASUREMENT: the point of a revision is
    -- that the old check measured something adjacent to the question. Saying
    -- which is what makes the gap findable by the next person.
    reason   TEXT NOT NULL,
    CHECK (length(trim(command)) >= 1),
    CHECK (length(trim(reason))  >= 1)
);

CREATE INDEX idx_check_revisions_gate ON check_revisions(gate_id, kind, id);

CREATE TRIGGER check_revisions_no_update BEFORE UPDATE ON check_revisions
BEGIN SELECT RAISE(ABORT, 'janus: a check revision is append-only; add another revision'); END;
CREATE TRIGGER check_revisions_no_delete BEFORE DELETE ON check_revisions
BEGIN SELECT RAISE(ABORT, 'janus: a check revision is append-only; the history is the point'); END;
