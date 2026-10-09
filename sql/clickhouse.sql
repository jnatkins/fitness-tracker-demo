CREATE DATABASE IF NOT EXISTS health_demo;
CREATE TABLE IF NOT EXISTS health_demo.foods (
 id String, name String, description String,
 calories Float64, protein Float64, carbs Float64, fat Float64,
 serving_grams Float64, vegan Bool, embedding Array(Float32),
 embedding_model String, content_hash String, version UInt64, deleted UInt8,
 INDEX food_vector embedding TYPE vector_similarity('hnsw', 'cosineDistance', 384)
) ENGINE = ReplacingMergeTree(version) ORDER BY id;
CREATE TABLE IF NOT EXISTS health_demo.meals (
 id String, user_id String, label String, logged_at DateTime64(3, 'UTC'),
 items String, calories Float64, protein Float64, carbs Float64, fat Float64,
 version UInt64, deleted UInt8
) ENGINE = ReplacingMergeTree(version) ORDER BY (user_id,id);
CREATE TABLE IF NOT EXISTS health_demo.activity (
 event_id UUID, user_id LowCardinality(String) CODEC(ZSTD(1)),
 recorded_at DateTime64(3, 'UTC') CODEC(Delta, ZSTD(1)),
 steps UInt32 CODEC(T64, ZSTD(1)), active_minutes Float64,
 heart_rate Nullable(UInt16) CODEC(T64, ZSTD(1)), device_id LowCardinality(String) CODEC(ZSTD(1)),
 original_recorded_at Nullable(DateTime64(3, 'UTC')) DEFAULT NULL,
 data_source LowCardinality(String) DEFAULT 'synthetic',
 active_zone_minutes UInt32 DEFAULT 0, heart_rate_samples UInt32 DEFAULT 0,
 utc_offset_minutes Int16 DEFAULT 0
) ENGINE = MergeTree ORDER BY (user_id,recorded_at,event_id);
CREATE TABLE IF NOT EXISTS health_demo.daily_activity (
 user_id String, day Date, steps UInt64, active_minutes Float64, samples UInt64
) ENGINE = SummingMergeTree ORDER BY (user_id,day);
CREATE MATERIALIZED VIEW IF NOT EXISTS health_demo.activity_rollup TO health_demo.daily_activity AS
 SELECT user_id,toDate(recorded_at) AS day,sum(steps) AS steps,
 sum(active_minutes) AS active_minutes,count() AS samples
 FROM health_demo.activity GROUP BY user_id,day;
CREATE TABLE IF NOT EXISTS health_demo.events (
 event_id UUID, run_id UUID, user_id LowCardinality(String) CODEC(ZSTD(1)),
 recorded_at DateTime64(3,'UTC') CODEC(Delta, ZSTD(1)),
 kind LowCardinality(String), tool LowCardinality(String) CODEC(ZSTD(1)),
 duration_ms Float64, detail String CODEC(ZSTD(1)), query_text String CODEC(ZSTD(3))
) ENGINE = MergeTree ORDER BY (user_id,recorded_at,event_id);
