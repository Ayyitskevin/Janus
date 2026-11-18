-- The migrator records this migration's checksum and commits the transaction.
BEGIN IMMEDIATE;

-- Reserve the key first and insert the gate in the same transaction. A separate
-- table keeps both current and previous gate readers' public records stable.
CREATE TABLE raise_requests (
    request_id TEXT PRIMARY KEY NOT NULL CHECK (
        typeof(request_id) = 'text'
        AND length(request_id) BETWEEN 1 AND 128
        AND instr(request_id, char(0)) = 0
        AND request_id GLOB '[A-Za-z0-9]*'
        AND request_id NOT GLOB '*[^A-Za-z0-9._:/-]*'
    ),
    gate_id TEXT NOT NULL UNIQUE REFERENCES gates(id) DEFERRABLE INITIALLY DEFERRED
) WITHOUT ROWID;

CREATE TRIGGER raise_requests_no_update BEFORE UPDATE ON raise_requests
BEGIN SELECT RAISE(ABORT, 'janus: a raise request is immutable'); END;
CREATE TRIGGER raise_requests_no_delete BEFORE DELETE ON raise_requests
BEGIN SELECT RAISE(ABORT, 'janus: a raise request is immutable'); END;

-- SQLite REPLACE can delete a conflicting row without invoking DELETE triggers.
CREATE TRIGGER raise_requests_no_replace BEFORE INSERT ON raise_requests
WHEN EXISTS (SELECT 1 FROM raise_requests
             WHERE request_id = NEW.request_id OR gate_id = NEW.gate_id)
BEGIN SELECT RAISE(ABORT, 'janus: a raise request is already associated'); END;
