-- Janus 0005 — shadow predictions are append-only and never terminal.
--
-- A prediction is a typed audit event linked to one earlier decision context.
-- The trigger refuses predictions after any terminal event, so evaluation can
-- only compare a prediction with a later human ruling. Nothing in this
-- migration writes rulings or grants a consumer authority.

CREATE TRIGGER decision_prediction_requires_open_gate BEFORE INSERT ON audit_events
WHEN NEW.verb = 'decision_prediction'
     AND (
         NEW.gate_id IS NULL
         OR NOT EXISTS (SELECT 1 FROM gates g WHERE g.id = NEW.gate_id)
         OR EXISTS (SELECT 1 FROM rulings r WHERE r.gate_id = NEW.gate_id)
     )
BEGIN
    SELECT RAISE(ABORT, 'janus: shadow prediction requires an existing open gate');
END;

CREATE TRIGGER decision_prediction_requires_json BEFORE INSERT ON audit_events
WHEN NEW.verb = 'decision_prediction'
     AND (NEW.detail IS NULL OR json_valid(NEW.detail) != 1)
BEGIN
    SELECT RAISE(ABORT, 'janus: shadow prediction must be valid JSON');
END;

CREATE TRIGGER decision_prediction_requires_v1_envelope BEFORE INSERT ON audit_events
WHEN NEW.verb = 'decision_prediction'
     AND json_valid(NEW.detail) = 1
     AND (
         json_extract(NEW.detail, '$.schema') IS NOT 'janus.decision-prediction.v1'
         OR json_extract(NEW.detail, '$.mode') IS NOT 'shadow'
         OR json_extract(NEW.detail, '$.verdict') IS NULL
         OR json_extract(NEW.detail, '$.verdict') NOT IN ('approve', 'deny', 'abstain')
         OR typeof(json_extract(NEW.detail, '$.context_event_id')) != 'integer'
         OR typeof(json_extract(NEW.detail, '$.context_sha256')) != 'text'
         OR length(json_extract(NEW.detail, '$.context_sha256')) != 64
         OR json_extract(NEW.detail, '$.context_sha256') GLOB '*[^0-9a-f]*'
     )
BEGIN
    SELECT RAISE(ABORT, 'janus: shadow prediction envelope is incompatible');
END;

CREATE TRIGGER decision_prediction_requires_matching_context BEFORE INSERT ON audit_events
WHEN NEW.verb = 'decision_prediction'
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
        'janus: shadow prediction must cite a context snapshot from the same gate');
END;
