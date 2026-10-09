-- Preserve duration precision and sorting key. Every existing run_id must be a UUID.
ALTER TABLE health_demo.events
 MODIFY COLUMN run_id UUID,
 MODIFY COLUMN user_id LowCardinality(String) CODEC(ZSTD(1)),
 MODIFY COLUMN tool LowCardinality(String) CODEC(ZSTD(1)),
 MODIFY COLUMN recorded_at DateTime64(3, 'UTC') CODEC(Delta, ZSTD(1)),
 MODIFY COLUMN detail String CODEC(ZSTD(1)),
 MODIFY COLUMN query_text String CODEC(ZSTD(3))
 SETTINGS mutations_sync=2;
-- One-off rewrite of this small demo table. Not scheduled maintenance.
OPTIMIZE TABLE health_demo.events FINAL SETTINGS optimize_skip_merged_partitions=0;
