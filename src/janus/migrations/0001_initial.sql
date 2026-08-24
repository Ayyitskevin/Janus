-- Janus 0001 — the gate ledger.
--
-- Append-only is enforced by the database, not by convention: every table here
-- carries triggers that RAISE(ABORT) on UPDATE and DELETE. Consumers cite gates
-- and rulings, so a row that can vanish is a citation that can rot (AGENTS.md,
-- "Deleting a gate or ruling must fail").
--
-- State is therefore NOT a mutable column. A gate's current state is derived
-- from its terminal event, because a column you can UPDATE is a column that can
-- hold two truths — and invariant one is that a gate is open or closed and
-- never both.

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------- gates ----
CREATE TABLE gates (
    id            TEXT PRIMARY KEY,
    raised_at     TEXT NOT NULL,
    raised_by     TEXT NOT NULL,          -- seat attribution: "<os_user>+<seat>"
    question      TEXT NOT NULL,
    kind          TEXT NOT NULL,
    decay         TEXT NOT NULL,          -- prose: what worsens while this waits
    decay_check   TEXT,                   -- optional re-runnable command
    consumer      TEXT NOT NULL,          -- who acts, and what they do with each outcome
    horizon       TEXT,                   -- optional ISO date; no invented deadlines
    delivery_check TEXT,                  -- optional: did the promised thing land
    binding_kind  TEXT,                   -- optional binding: {kind, locator, sha256}
    binding_locator TEXT,
    binding_sha256  TEXT,
    cites         TEXT,                   -- optional gate id this one re-raises

    -- The four required fields are refused at the schema, not just in Python:
    -- a gate without a consumer is a note, one without decay has no claim on
    -- attention, and one whose question is a paragraph has not been thought
    -- through. ADR 0001 says the schema refuses a degraded record.
    CHECK (length(trim(question)) BETWEEN 1 AND 280),
    CHECK (length(trim(decay))    >= 1),
    CHECK (length(trim(consumer)) >= 1),
    CHECK (kind IN ('irreversible', 'authority', 'taste', 'resource')),
    -- A binding is all three parts or none of them.
    CHECK ((binding_kind IS NULL AND binding_locator IS NULL AND binding_sha256 IS NULL)
        OR (binding_kind IS NOT NULL AND binding_locator IS NOT NULL AND binding_sha256 IS NOT NULL)),
    CHECK (binding_sha256 IS NULL OR length(binding_sha256) = 64),
    FOREIGN KEY (cites) REFERENCES gates(id)
);

-- ------------------------------------------------------------- options ----
-- Ordered alternatives. Six of twelve real gates needed these; empty means
-- plain approve/refuse.
CREATE TABLE gate_options (
    gate_id     TEXT NOT NULL REFERENCES gates(id),
    option_id   TEXT NOT NULL,
    position    INTEGER NOT NULL,
    label       TEXT NOT NULL,
    detail      TEXT,
    recommended INTEGER NOT NULL DEFAULT 0 CHECK (recommended IN (0, 1)),
    PRIMARY KEY (gate_id, option_id),
    CHECK (length(trim(label)) >= 1)
);

-- ------------------------------------------------------------ terminals ----
-- One terminal event per gate. The UNIQUE constraint on gate_id is what makes
-- "open or closed, never both" a database fact rather than an application
-- promise: a second terminal event cannot be written at all.
--
-- Only `approved` and `refused` are rulings (a human ruled). `expired`,
-- `withdrawn` and `superseded` are terminal because nobody ruled — the corpus
-- showed `superseded` is the most common ending of all.
CREATE TABLE rulings (
    gate_id     TEXT PRIMARY KEY REFERENCES gates(id),
    state       TEXT NOT NULL CHECK (state IN
                    ('approved', 'refused', 'expired', 'withdrawn', 'superseded')),
    ruled_at    TEXT NOT NULL,
    ruled_by    TEXT NOT NULL,
    reason      TEXT NOT NULL,
    option_id   TEXT,                     -- required when the gate has options and state=approved
    -- The digest observed AT RULING TIME. A ruling approves specific bytes; if
    -- the artifact later changes the ruling does not follow it.
    bound_sha256 TEXT,
    CHECK (length(trim(reason)) >= 1),
    CHECK (bound_sha256 IS NULL OR length(bound_sha256) = 64)
);

-- --------------------------------------------------------------- audit ----
-- Every mutation appends here, including the ones that wrote the tables above.
-- This is the feedback surface (AGENTS.md invariant two).
CREATE TABLE audit_events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    at        TEXT NOT NULL,
    actor     TEXT NOT NULL,
    verb      TEXT NOT NULL,
    gate_id   TEXT,
    detail    TEXT
);

-- ---------------------------------------------------- observations ----
-- decay/delivery check results. An observation is taken at a moment and NEVER
-- changes state (AGENTS.md invariant four: a stale check must read as stale).
CREATE TABLE observations (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    gate_id   TEXT NOT NULL REFERENCES gates(id),
    at        TEXT NOT NULL,
    kind      TEXT NOT NULL CHECK (kind IN ('decay', 'delivery')),
    command   TEXT NOT NULL,
    exit_code INTEGER NOT NULL,
    note      TEXT
);

-- `schema_migrations` is owned by the migrator (core.migrate), not by a
-- migration — a migration must never create the table that records it.

-- -------------------------------------------------------------- indexes ----
CREATE INDEX idx_rulings_state ON rulings(state);
CREATE INDEX idx_gates_raised_at ON gates(raised_at);
CREATE INDEX idx_observations_gate ON observations(gate_id, at);

-- ------------------------------------------------------ append-only ----
-- The ledger tables refuse UPDATE and DELETE outright. Corrections extend the
-- record: a reversal is a NEW gate citing the prior one, never an edit.
CREATE TRIGGER gates_no_update BEFORE UPDATE ON gates
BEGIN SELECT RAISE(ABORT, 'janus: gates are append-only; raise a new gate that cites this one'); END;
CREATE TRIGGER gates_no_delete BEFORE DELETE ON gates
BEGIN SELECT RAISE(ABORT, 'janus: gates are append-only; consumers cite them'); END;

CREATE TRIGGER rulings_no_update BEFORE UPDATE ON rulings
BEGIN SELECT RAISE(ABORT, 'janus: a ruling is terminal and append-only'); END;
CREATE TRIGGER rulings_no_delete BEFORE DELETE ON rulings
BEGIN SELECT RAISE(ABORT, 'janus: a ruling is terminal and append-only'); END;

CREATE TRIGGER options_no_update BEFORE UPDATE ON gate_options
BEGIN SELECT RAISE(ABORT, 'janus: gate options are fixed once raised'); END;
CREATE TRIGGER options_no_delete BEFORE DELETE ON gate_options
BEGIN SELECT RAISE(ABORT, 'janus: gate options are fixed once raised'); END;

CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit_events
BEGIN SELECT RAISE(ABORT, 'janus: the audit trail is append-only'); END;
CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit_events
BEGIN SELECT RAISE(ABORT, 'janus: the audit trail is append-only'); END;

CREATE TRIGGER observations_no_update BEFORE UPDATE ON observations
BEGIN SELECT RAISE(ABORT, 'janus: an observation records a moment; take a new one'); END;
CREATE TRIGGER observations_no_delete BEFORE DELETE ON observations
BEGIN SELECT RAISE(ABORT, 'janus: an observation records a moment; take a new one'); END;

-- A ruling that names an option must name one this gate actually offers, and a
-- gate WITH options cannot be approved without naming one. Enforced at write
-- time because ADR 0001 says such a ruling is refused, not stored and flagged.
CREATE TRIGGER ruling_option_must_exist BEFORE INSERT ON rulings
WHEN NEW.option_id IS NOT NULL
     AND NOT EXISTS (SELECT 1 FROM gate_options o
                     WHERE o.gate_id = NEW.gate_id AND o.option_id = NEW.option_id)
BEGIN SELECT RAISE(ABORT, 'janus: ruling names an option this gate does not offer'); END;

CREATE TRIGGER ruling_option_required BEFORE INSERT ON rulings
WHEN NEW.state = 'approved'
     AND NEW.option_id IS NULL
     AND EXISTS (SELECT 1 FROM gate_options o WHERE o.gate_id = NEW.gate_id)
BEGIN SELECT RAISE(ABORT, 'janus: this gate offers options; an approval must name exactly one'); END;
