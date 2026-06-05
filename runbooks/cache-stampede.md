# Runbook: Cache Stampede

## Symptoms
- Sudden spike in DB queries (10-100x normal rate) exactly when staleness alert fires
- Staleness briefly spikes to very high value, then drops to near-zero
- Multiple services simultaneously report cache miss
- Redis memory drops suddenly (many keys expiring at same time)
- DB CPU spikes to 100%, causing cascading failures

## Root Cause
A cache stampede (also called "thundering herd") occurs when:
1. A popular cached key expires or is deleted
2. Many concurrent requests all find the cache empty simultaneously
3. All of them simultaneously query the DB and try to repopulate the cache
4. The DB becomes overwhelmed, causing slow responses or failures

This is most severe when:
- Keys have synchronized TTLs (all set at the same time with same TTL)
- A manual cache flush or deployment happened
- A single key is accessed by thousands of requests per second

## Diagnosis Steps
```bash
# Check for correlated key expiry
redis-cli DEBUG SLEEP 0  # Force event processing
redis-cli INFO keyspace   # Check db0 keys count — sudden drop = stampede

# Monitor expiry events
redis-cli CONFIG SET notify-keyspace-events Ex
redis-cli SUBSCRIBE __keyevent@0__:expired | head -50

# Check DB slow queries during the spike
# (Check your DB's slow query log)

# Look for correlated staleness in the dashboard
# Multiple keys from same service all breaching simultaneously
```

## Immediate Fix — Break the Stampede

### Option 1: Mutex Lock (Distributed Lock)
```python
import redis
import time

def get_with_lock(redis_client, key, fetch_fn, ttl=300, lock_timeout=10):
    """Get from cache with mutex to prevent stampede."""
    value = redis_client.get(key)
    if value:
        return value

    lock_key = f"lock:{key}"
    lock_acquired = redis_client.set(lock_key, "1", nx=True, ex=lock_timeout)

    if lock_acquired:
        try:
            value = fetch_fn()  # Fetch from DB
            redis_client.setex(key, ttl, value)
            return value
        finally:
            redis_client.delete(lock_key)
    else:
        # Wait for the lock holder to populate the cache
        for _ in range(20):  # Max 2 seconds wait
            time.sleep(0.1)
            value = redis_client.get(key)
            if value:
                return value
        return fetch_fn()  # Fallback
```

### Option 2: Probabilistic Early Expiration (XFetch)
```python
import math, random, time

def get_with_early_expiry(redis_client, key, fetch_fn, ttl=300, beta=1.0):
    """XFetch algorithm — recompute before expiry to prevent stampede."""
    value, expiry = redis_client.get(key), redis_client.expiretime(key)
    if value:
        now = time.time()
        # Probabilistically recompute as expiry approaches
        if now - beta * math.log(random.random()) >= expiry:
            value = None  # Trigger early recompute
    if not value:
        value = fetch_fn()
        redis_client.setex(key, ttl, value)
    return value
```

### Option 3: Stagger TTLs
```python
import random

# Instead of: redis.setex(key, 300, value)
# Add jitter:
jitter = random.randint(-30, 30)  # ±30 second jitter
redis.setex(key, 300 + jitter, value)
```

## Long-term Fix
1. **Jitter all TTLs** by ±10-20% to prevent synchronized expiry
2. **Implement XFetch** (probabilistic early expiration) for high-traffic keys
3. **Use background refresh**: Refresh cache asynchronously before TTL expires (read-through with async update)
4. **Consider cache warming**: Pre-populate on deployment before traffic hits

## Prevention
- Never use `FLUSHDB` / `FLUSHALL` in production without a warm-up plan
- After deployments, warm critical cache keys before cutting traffic
- Monitor `redis_keyspace_misses` — a spike > 10x baseline indicates stampede

## Related Alerts
- DB CPU > 80% correlated with cache miss spike
- `redis_keyspace_misses` spike > 10x baseline
- Multiple keys from same service all hitting SLA breach simultaneously
