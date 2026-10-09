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
CREATE TABLE IF NOT EXISTS change_outbox (
 id bigserial PRIMARY KEY, entity text NOT NULL, record_id text NOT NULL,
 payload jsonb NOT NULL, deleted boolean NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), processed_at timestamptz
);
CREATE OR REPLACE FUNCTION capture_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP = 'DELETE' THEN
  INSERT INTO change_outbox(entity,record_id,payload,deleted) VALUES(TG_TABLE_NAME,OLD.id,to_jsonb(OLD),true);
  RETURN OLD;
 ELSE
  INSERT INTO change_outbox(entity,record_id,payload,deleted) VALUES(TG_TABLE_NAME,NEW.id,to_jsonb(NEW),false);
  RETURN NEW;
 END IF;
END $$;
DROP TRIGGER IF EXISTS foods_cdc ON foods;
CREATE TRIGGER foods_cdc AFTER INSERT OR UPDATE OR DELETE ON foods FOR EACH ROW EXECUTE FUNCTION capture_change();
DROP TRIGGER IF EXISTS meals_cdc ON meals;
CREATE TRIGGER meals_cdc AFTER INSERT OR UPDATE OR DELETE ON meals FOR EACH ROW EXECUTE FUNCTION capture_change();

CREATE TABLE IF NOT EXISTS removed_meals (LIKE meals INCLUDING ALL);
