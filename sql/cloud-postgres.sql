-- Fresh dedicated Cloud demo database; no local outbox triggers.
CREATE TABLE IF NOT EXISTS foods (
 id text PRIMARY KEY, name text NOT NULL, description text NOT NULL,
 calories double precision NOT NULL, protein double precision NOT NULL,
 carbs double precision NOT NULL, fat double precision NOT NULL,
 serving_grams double precision NOT NULL, vegan boolean NOT NULL DEFAULT false
);
CREATE TABLE IF NOT EXISTS meals (
 id text PRIMARY KEY, user_id text NOT NULL, label text NOT NULL,
 logged_at timestamptz NOT NULL, items jsonb NOT NULL,
 calories double precision NOT NULL, protein double precision NOT NULL,
 carbs double precision NOT NULL, fat double precision NOT NULL
);
CREATE TABLE IF NOT EXISTS drafts (
 id text PRIMARY KEY, user_id text NOT NULL, label text NOT NULL, items jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), confirmed boolean NOT NULL DEFAULT false
);

ALTER TABLE foods ADD COLUMN IF NOT EXISTS embedding real[];
ALTER TABLE foods ADD COLUMN IF NOT EXISTS embedding_model text;
ALTER TABLE foods ADD COLUMN IF NOT EXISTS embedding_text text;
CREATE TABLE IF NOT EXISTS removed_meals (LIKE meals INCLUDING ALL);
