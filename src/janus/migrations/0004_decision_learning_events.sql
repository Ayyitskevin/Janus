-- Janus 0004 — decision-learning records are typed, append-only audit events.
--
-- This deliberately adds no mutable policy table. A context event snapshots the
-- facts known BEFORE a ruling; a feedback event links the human label to that
-- exact snapshot AFTER the ruling. Both already inherit audit_events' UPDATE and
-- DELETE refusals and its stable export coverage.
--
-- JSON shape and SHA-256 verification live in core.py, where canonical JSON is
-- available. These triggers enforce the sequencing and linkage invariants for
-- every SQLite writer, including one that bypasses the CLI.

CREATE TRIGGER decision_context_requires_open_gate BEFORE INSERT ON audit_events
WHEN NEW.verb = 'decision_context'
     AND (
         NEW.gate_id IS NULL
         OR NOT EXISTS (SELECT 1 FROM gates g WHERE g.id = NEW.gate_id)
         OR EXISTS (SELECT 1 FROM rulings r WHERE r.gate_id = NEW.gate_id)
     )
BEGIN
    SELECT RAISE(ABORT,
        'janus: decision context must snapshot an existing open gate');
END;

CREATE TRIGGER decision_context_requires_json BEFORE INSERT ON audit_events
WHEN NEW.verb = 'decision_context'
     AND (NEW.detail IS NULL OR json_valid(NEW.detail) != 1)
BEGIN
    SELECT RAISE(ABORT, 'janus: decision context must be valid JSON');
END;

CREATE TRIGGER decision_context_requires_v1_envelope BEFORE INSERT ON audit_events
WHEN NEW.verb = 'decision_context'
     AND json_valid(NEW.detail) = 1
     AND (
         json_extract(NEW.detail, '$.schema') IS NOT 'janus.decision-context-event.v1'
         OR typeof(json_extract(NEW.detail, '$.context_sha256')) != 'text'
         OR length(json_extract(NEW.detail, '$.context_sha256')) != 64
         OR json_extract(NEW.detail, '$.context_sha256') GLOB '*[^0-9a-f]*'
         OR json_type(NEW.detail, '$.context') IS NOT 'object'
     )
BEGIN
    SELECT RAISE(ABORT, 'janus: decision context envelope is incompatible');
END;

CREATE TRIGGER decision_feedback_requires_json BEFORE INSERT ON audit_events
WHEN NEW.verb = 'decision_feedback'
     AND (NEW.detail IS NULL OR json_valid(NEW.detail) != 1)
BEGIN
    SELECT RAISE(ABORT, 'janus: decision feedback must be valid JSON');
END;

CREATE TRIGGER decision_feedback_requires_human_ruling BEFORE INSERT ON audit_events
WHEN NEW.verb = 'decision_feedback'
     AND (
         NEW.gate_id IS NULL
         OR NOT EXISTS (
             SELECT 1 FROM rulings r
             WHERE r.gate_id = NEW.gate_id
               AND r.state = json_extract(NEW.detail, '$.outcome')
               AND r.ruled_by = NEW.actor
         )
     )
BEGIN
    SELECT RAISE(ABORT,
        'janus: decision feedback requires an approved or refused human ruling');
END;

CREATE TRIGGER decision_feedback_requires_v1_envelope BEFORE INSERT ON audit_events
WHEN NEW.verb = 'decision_feedback'
     AND json_valid(NEW.detail) = 1
     AND (
         json_extract(NEW.detail, '$.schema') IS NOT 'janus.decision-feedback.v1'
         OR typeof(json_extract(NEW.detail, '$.context_event_id')) != 'integer'
         OR typeof(json_extract(NEW.detail, '$.context_sha256')) != 'text'
         OR length(json_extract(NEW.detail, '$.context_sha256')) != 64
         OR json_extract(NEW.detail, '$.context_sha256') GLOB '*[^0-9a-f]*'
         OR json_extract(NEW.detail, '$.outcome') IS NULL
         OR json_extract(NEW.detail, '$.outcome') NOT IN ('approved', 'refused')
         OR json_type(NEW.detail, '$.reason_codes') IS NOT 'array'
         OR json_array_length(NEW.detail, '$.reason_codes') NOT BETWEEN 1 AND 8
     )
BEGIN
    SELECT RAISE(ABORT, 'janus: decision feedback envelope is incompatible');
END;

CREATE TRIGGER decision_feedback_requires_matching_context BEFORE INSERT ON audit_events
WHEN NEW.verb = 'decision_feedback'
     AND json_valid(NEW.detail) = 1
     AND NOT EXISTS (
         SELECT 1 FROM audit_events c
         WHERE c.id = json_extract(NEW.detail, '$.context_event_id')
           AND c.gate_id = NEW.gate_id
           AND c.verb = 'decision_context'
           AND json_extract(c.detail, '$.context_sha256') =
               json_extract(NEW.detail, '$.context_sha256')
     )
BEGIN
    SELECT RAISE(ABORT,
        'janus: decision feedback must cite a context snapshot from the same gate');
END;

CREATE TRIGGER decision_feedback_one_per_ruling BEFORE INSERT ON audit_events
WHEN NEW.verb = 'decision_feedback'
     AND EXISTS (
         SELECT 1 FROM audit_events f
         WHERE f.gate_id = NEW.gate_id AND f.verb = 'decision_feedback'
     )
BEGIN
    SELECT RAISE(ABORT, 'janus: a human ruling has one decision feedback record');
END;
