CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS signal_trace_knowledge_config (
    singleton boolean PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    model_id text NOT NULL,
    dimensions integer NOT NULL CHECK (dimensions > 0)
);
CREATE TABLE IF NOT EXISTS signal_trace_knowledge_chunks (
    chunk_id text PRIMARY KEY,
    document_id text NOT NULL,
    source text NOT NULL,
    services text[] NOT NULL,
    content text NOT NULL,
    embedding vector NOT NULL,
    metadata jsonb NOT NULL
);
