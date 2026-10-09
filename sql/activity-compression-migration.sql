-- Apply once to the existing activity table. Preserve values, sort order, and MV.
ALTER TABLE health_demo.activity
 MODIFY COLUMN user_id LowCardinality(String) CODEC(ZSTD(1)),
 MODIFY COLUMN device_id LowCardinality(String) CODEC(ZSTD(1)),
 MODIFY COLUMN recorded_at DateTime64(3, 'UTC') CODEC(Delta, ZSTD(1)),
 MODIFY COLUMN steps UInt32 CODEC(T64, ZSTD(1)),
 MODIFY COLUMN heart_rate UInt16 CODEC(T64, ZSTD(1))
 SETTINGS mutations_sync=2;
-- One-off rewrite of this small demo table for historical codec changes.
-- Do not schedule this as routine maintenance on a large production table.
OPTIMIZE TABLE health_demo.activity FINAL SETTINGS optimize_skip_merged_partitions=0;
