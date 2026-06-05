# Runbook: Invalidation Handler Timeout

## Symptoms
- Specific keys are stale (not all keys — points to invalidation pipeline, not Redis itself)
- Staleness grows monotonically without recovery
- SQS queue depth rising for the invalidation queue
- CloudWatch: `ApproximateNumberOfMessagesNotVisible` high on invalidation queue
- Application logs: `TimeoutError` or `MessageProcessingError` from cache invalidation service

## Root Cause
Cache invalidation handlers consume events (from SQS, Kafka, or webhooks) that trigger cache writes. Timeouts occur when:
1. **Downstream DB slow**: Invalidation handler fetches fresh data from the DB, but DB is under load
2. **Lambda cold start**: If invalidation runs in Lambda, cold starts add 500-2000ms latency
3. **Message backlog**: Queue has grown large; handler is processing stale messages
4. **Handler crash loop**: Handler crashes and messages are being retried with backoff
5. **Missing DLQ**: Failed messages are being retried indefinitely instead of sent to DLQ

## Diagnosis Steps
```bash
# Check SQS queue depth
aws sqs get-queue-attributes \
  --queue-url $INVALIDATION_QUEUE_URL \
  --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible

# Check Lambda error rate (if handler is Lambda)
aws cloudwatch get-metric-statistics \
  --namespace AWS/Lambda \
  --metric-name Errors \
  --dimensions Name=FunctionName,Value=cache-invalidation-handler \
  --start-time $(date -u -d '-1 hour' '+%Y-%m-%dT%H:%M:%SZ') \
  --end-time $(date -u '+%Y-%m-%dT%H:%M:%SZ') \
  --period 300 \
  --statistics Sum

# Check DLQ depth
aws sqs get-queue-attributes \
  --queue-url $INVALIDATION_DLQ_URL \
  --attribute-names ApproximateNumberOfMessages
```

## Fix Steps
1. **Immediate — purge old queue messages** (if backlog is too old to be useful):
   ```bash
   aws sqs purge-queue --queue-url $INVALIDATION_QUEUE_URL
   ```
   ⚠️ Only do this if you accept stale cache until next write cycle.

2. **Scale up consumers**:
   ```bash
   # Increase Lambda concurrency
   aws lambda put-function-concurrency \
     --function-name cache-invalidation-handler \
     --reserved-concurrent-executions 50
   ```

3. **Force cache refresh** for critical keys:
   ```bash
   redis-cli DEL payments:user:1234:cart  # Forces next read to reload
   ```

4. **Increase handler timeout** if DB is slow:
   ```python
   # In Lambda handler
   context.timeout = 30  # seconds
   ```

## Prevention
- Always configure a DLQ on the invalidation queue
- Set `VisibilityTimeout` to at least 2x handler timeout
- Monitor queue depth with alert at > 1000 messages
- Use exponential backoff in the handler

## Related Alerts
- SQS `ApproximateNumberOfMessages > 1000`
- Lambda `Errors > 10 in 5 minutes`
- Monotonically increasing staleness for a single service's keys
