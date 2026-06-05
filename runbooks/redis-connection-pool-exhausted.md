# Runbook: Redis Connection Pool Exhausted

## Symptoms
- Redis commands timing out or returning `CONNECTIONREFUSED` / `Too many connections`
- `redis_connected_clients` metric is near or at `maxclients` limit (default: 10,000)
- Applications report `redis.exceptions.ConnectionError` in logs
- Staleness spikes across ALL keys simultaneously (not just one service)

## Root Cause
Connection pool exhaustion occurs when the total number of concurrent Redis connections exceeds the server limit. Common causes:
1. **Connection leak**: Application code opens connections but never releases them (missing `connection.close()` or no connection pool configured)
2. **Pool misconfiguration**: Pool `max_connections` is too high per instance, causing aggregate overuse
3. **Traffic spike**: Sudden increase in application instances without adjusting the pool
4. **Long-lived connections**: Blocking operations (BLPOP, SUBSCRIBE) holding connections indefinitely

## Diagnosis Steps
```bash
# Check current connection count
redis-cli INFO clients | grep connected_clients

# Check max connections configured
redis-cli CONFIG GET maxclients

# List all client connections
redis-cli CLIENT LIST

# Kill idle connections (>60s)
redis-cli CLIENT NO-EVICT ON
```

## Fix Steps
1. **Immediate**: Restart the application instance with the highest connection count
2. **Configure connection pool** in application code:
   ```python
   pool = redis.ConnectionPool(host='localhost', port=6379, max_connections=20)
   r = redis.Redis(connection_pool=pool)
   ```
3. **Increase server limit** (temporary relief):
   ```bash
   redis-cli CONFIG SET maxclients 20000
   ```
4. **Long-term**: Implement connection pooling at the load balancer level using Redis Cluster or a proxy like Envoy

## Prevention
- Set `max_connections` per application instance to `(maxclients / num_instances) * 0.8`
- Monitor `redis_connected_clients` with an alert at 80% of `maxclients`
- Use connection pooling library (e.g., `redis-py` pool, `ioredis` pool)
- Enable `tcp-keepalive 60` in redis.conf to clean dead connections

## Related Alerts
- `redis_connected_clients > 8000`
- Staleness spike across multiple services simultaneously
