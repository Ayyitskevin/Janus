-- The migrator records this migration's checksum and commits the transaction.
BEGIN IMMEDIATE;

ALTER TABLE gates ADD COLUMN request_id TEXT CHECK (
    request_id IS NULL OR (
        typeof(request_id) = 'text'
        AND length(request_id) BETWEEN 1 AND 128
        AND instr(request_id, char(0)) = 0
        AND request_id GLOB '[A-Za-z0-9]*'
        AND request_id NOT GLOB '*[^A-Za-z0-9._:/-]*'
    )
);

CREATE UNIQUE INDEX idx_gates_request_id ON gates(request_id)
WHERE request_id IS NOT NULL;
