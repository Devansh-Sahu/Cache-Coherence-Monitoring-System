# Runbook: TTL Misconfiguration

## Symptoms
- Keys expire before their expected SLA window
- `ttl_remaining_s` is very low (< 5s) or -2 (key gone) when staleness alert fires
- Cache hit rate drops sharply (`redis_keyspace_hits / (hits + misses)`)
- Application falls back to DB frequently, causing DB load spike
- Staleness alternates between 0ms and very high values (expiry/recreation cycle)

## Root Cause
TTL (Time To Live) misconfiguration causes keys to expire before they should, or prevents proper expiry allowing data to become very stale:
1. **TTL too short**: Set in seconds but code expects milliseconds (off by 1000x)
2. **TTL not set**: Key persists forever; `last_write` shadow key is never updated
3. **TTL reset on read**: Code accidentally calls `SET` instead of `SETEX` during read
4. **Write race**: Multiple writers with different TTLs, shorter TTL wins
5. **Clock skew**: Server clock drift causing premature expiry

## Diagnosis Steps
```bash
# Check current TTL of the problematic key
redis-cli TTL <key_name>
redis-cli PTTL <key_name>  # Millisecond precision

# Check shadow key (should match last write time)
redis-cli GET __meta:<key_name>:last_write

# Monitor TTL changes in real-time
redis-cli MONITOR | grep -E "(SET|EXPIRE|SETEX|PEXPIRE) <key_name>"

# Check keyspace events for expiry
redis-cli CONFIG SET notify-keyspace-events Ex
redis-cli SUBSCRIBE __keyevent@0__:expired
```

## Fix Steps
1. **Identify the writer**: Trace which service is calling SET/SETEX on this key
   ```bash
   redis-cli MONITOR | grep <key_name>
   ```

2. **Correct the TTL** in the writer code:
   ```python
   # Wrong: TTL in ms when command expects seconds
   redis.setex(key, 5000, value)  # Expires in 83 minutes, not 5 seconds!

   # Correct:
   redis.setex(key, 5, value)       # 5 seconds
   redis.psetex(key, 5000, value)   # 5000 milliseconds
   ```

3. **Fix missing TTL**: Ensure every write includes a TTL:
   ```python
   # Never use SET without EXPIRE for cache keys
   redis.set(key, value, ex=ttl_seconds)  # Use ex= parameter
   ```

4. **Prevent TTL reset on read**:
   ```python
   # Wrong — resets TTL on every read
   redis.set(key, redis.get(key))

   # Correct — use GETEX to extend TTL only if needed
   value = redis.getex(key, exat=expiry_timestamp)
   ```

## Prevention
- Define TTL as a named constant in the service code; never hardcode inline
- Add unit tests that assert `redis.ttl(key) == expected_ttl` after a write
- Monitor `redis_keyspace_misses` — a spike indicates excessive expiry
- The shadow key `__meta:<key>:last_write` helps distinguish "expired" from "stale"

## Related Alerts
- `ttl_remaining_s < 10` when staleness alert fires
- Cache hit rate drop below 80%
- DB connection pool saturation (caused by fallback reads)
