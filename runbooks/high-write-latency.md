# Runbook: High Write Latency

## Symptoms
- `__meta:<key>:last_write` shadow key timestamp lags behind real-time
- Staleness grows gradually, not abruptly (write is happening, just slowly)
- Redis `redis_commands_duration_seconds` P99 latency is elevated
- `redis_blocked_clients` metric is non-zero
- Occasional `LOADING` or `READONLY` errors

## Root Cause
Write latency spikes cause the cache producer to write infrequently or slowly, increasing staleness:
1. **Redis CPU-bound**: Expensive commands (SORT, KEYS, large LRANGE) blocking the event loop
2. **AOF/RDB persistence**: `appendfsync always` or background save causing I/O stalls
3. **Network latency**: Application-to-Redis network degraded (check VPC routing, cross-AZ)
4. **Large values**: Writing multi-MB values causing serialization + network bottleneck
5. **Lua script blocking**: Long-running Lua scripts hold the server lock
6. **Cluster replication lag**: In Redis Cluster, replica sync adds write latency

## Diagnosis Steps
```bash
# Check Redis latency
redis-cli --latency -i 1
redis-cli --latency-history -i 1

# Check slow log (commands > 10ms)
redis-cli CONFIG SET slowlog-log-slower-than 10000  # 10ms threshold
redis-cli SLOWLOG GET 10

# Check server info
redis-cli INFO stats | grep -E "(total_commands|rejected_connections|io_threads)"
redis-cli INFO persistence | grep -E "(rdb_|aof_)"

# Check memory fragmentation
redis-cli INFO memory | grep mem_fragmentation_ratio

# Find large keys
redis-cli --bigkeys
```

## Fix Steps
1. **Immediate — switch to background persistence**:
   ```bash
   redis-cli CONFIG SET appendfsync everysec  # From 'always' to 'everysec'
   redis-cli CONFIG SET save ""               # Disable RDB snapshots
   ```

2. **Kill blocking commands**:
   ```bash
   # Find slow operations
   redis-cli CLIENT LIST | grep -E "cmd=(KEYS|SORT|LRANGE)"
   # Kill them
   redis-cli CLIENT KILL ID <client-id>
   ```

3. **Reduce value sizes**: Compress large values before writing:
   ```python
   import zlib, json
   compressed = zlib.compress(json.dumps(data).encode())
   redis.set(key, compressed)
   ```

4. **Use pipeline for bulk writes**:
   ```python
   with redis.pipeline() as pipe:
       for key, value in updates.items():
           pipe.set(key, value, ex=ttl)
       pipe.execute()
   ```

5. **Enable I/O threads** (Redis 6+):
   ```bash
   redis-cli CONFIG SET io-threads 4
   redis-cli CONFIG SET io-threads-do-reads yes
   ```

## Prevention
- Set `slowlog-log-slower-than 1000` (1ms) and monitor slow log count
- Alert when P99 write latency > 10ms
- Never use `KEYS *` in production — use `SCAN` with cursor instead
- Implement value size limits: reject writes > 1MB

## Related Alerts
- `redis_commands_duration_seconds{quantile="0.99"} > 0.01`
- `redis_blocked_clients > 0` for > 30 seconds
