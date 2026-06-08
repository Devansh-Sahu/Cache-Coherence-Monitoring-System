"""
AWS Production Setup Script — Cache Staleness Monitor
=====================================================
Sets up ALL real AWS resources using boto3 directly.
No Docker, no Terraform CLI, no LocalStack needed.

Usage:
    python scripts/aws_setup.py

Requires:
    - AWS credentials in .env (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY)
    - GROQ_API_KEY in .env
    - SLACK_WEBHOOK_URL in .env
"""

from __future__ import annotations

import io
import json
import os
import sys
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from dotenv import load_dotenv
load_dotenv()

import boto3
from botocore.exceptions import ClientError

# ── Config ────────────────────────────────────────────────────────────────────
REGION       = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
ACCESS_KEY   = os.environ.get("AWS_ACCESS_KEY_ID", "")
SECRET_KEY   = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
GROQ_KEY     = os.environ.get("GROQ_API_KEY", "")
SLACK_URL    = os.environ.get("SLACK_WEBHOOK_URL", "")

REGISTRY_TABLE  = "CacheKeyRegistry"
HISTORY_TABLE   = "StalenessHistory"
S3_BUCKET       = "csm-runbooks"
SQS_QUEUE       = "csm-alerts"
SQS_DLQ         = "csm-alerts-dlq"
LAMBDA_NAME     = "csm-staleness-alerter"
LAMBDA_ROLE     = "csm-lambda-role"
SECRET_NAME     = "csm/groq-api-key"
CW_RULE_NAME    = "csm-staleness-check"

ROOT = Path(__file__).parents[1]

OK   = "[OK]"
SKIP = "[SKIP]"
FAIL = "[FAIL]"


def _client(service: str):
    return boto3.client(
        service,
        region_name=REGION,
        aws_access_key_id=ACCESS_KEY,
        aws_secret_access_key=SECRET_KEY,
    )


def _resource(service: str):
    return boto3.resource(
        service,
        region_name=REGION,
        aws_access_key_id=ACCESS_KEY,
        aws_secret_access_key=SECRET_KEY,
    )


def _get_account_id() -> str:
    sts = _client("sts")
    return sts.get_caller_identity()["Account"]


# ── Step 1: DynamoDB Tables ───────────────────────────────────────────────────

def setup_dynamodb() -> None:
    print("\n[1/8] DynamoDB tables")
    db = _resource("dynamodb")
    existing = {t.name for t in db.tables.all()}

    # CacheKeyRegistry
    if REGISTRY_TABLE not in existing:
        db.create_table(
            TableName=REGISTRY_TABLE,
            KeySchema=[{"AttributeName": "key_name", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "key_name",       "AttributeType": "S"},
                {"AttributeName": "owning_service",  "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
            GlobalSecondaryIndexes=[{
                "IndexName": "owning_service-index",
                "KeySchema": [{"AttributeName": "owning_service", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            }],
            Tags=[{"Key": "Project", "Value": "csm"}],
        )
        print(f"  {OK}  Created {REGISTRY_TABLE}")
    else:
        print(f"  {SKIP} {REGISTRY_TABLE} already exists")

    # StalenessHistory
    if HISTORY_TABLE not in existing:
        table = db.create_table(
            TableName=HISTORY_TABLE,
            KeySchema=[
                {"AttributeName": "key_name",  "KeyType": "HASH"},
                {"AttributeName": "timestamp", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "key_name",       "AttributeType": "S"},
                {"AttributeName": "timestamp",       "AttributeType": "S"},
                {"AttributeName": "owning_service",  "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
            GlobalSecondaryIndexes=[{
                "IndexName": "owning_service-timestamp-index",
                "KeySchema": [
                    {"AttributeName": "owning_service", "KeyType": "HASH"},
                    {"AttributeName": "timestamp",       "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }],
            Tags=[{"Key": "Project", "Value": "csm"}],
        )
        # Enable TTL
        table.meta.client.update_time_to_live(
            TableName=HISTORY_TABLE,
            TimeToLiveSpecification={"Enabled": True, "AttributeName": "expires_at"},
        )
        print(f"  {OK}  Created {HISTORY_TABLE} (TTL enabled)")
    else:
        print(f"  {SKIP} {HISTORY_TABLE} already exists")


# ── Step 2: S3 Bucket ─────────────────────────────────────────────────────────

def setup_s3() -> None:
    print("\n[2/8] S3 bucket")
    s3 = _client("s3")
    try:
        if REGION == "us-east-1":
            s3.create_bucket(Bucket=S3_BUCKET)
        else:
            s3.create_bucket(
                Bucket=S3_BUCKET,
                CreateBucketConfiguration={"LocationConstraint": REGION},
            )
        # Block all public access
        s3.put_public_access_block(
            Bucket=S3_BUCKET,
            PublicAccessBlockConfiguration={
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            },
        )
        print(f"  {OK}  Created s3://{S3_BUCKET}")
    except ClientError as e:
        if "BucketAlreadyOwnedByYou" in str(e) or "BucketAlreadyExists" in str(e):
            print(f"  {SKIP} s3://{S3_BUCKET} already exists")
        else:
            raise


# ── Step 3: SQS Queues ────────────────────────────────────────────────────────

def setup_sqs() -> str:
    print("\n[3/8] SQS queues")
    sqs = _client("sqs")

    # DLQ first
    dlq_url = ""
    dlq_arn = ""
    try:
        resp = sqs.create_queue(QueueName=SQS_DLQ)
        dlq_url = resp["QueueUrl"]
        dlq_arn = sqs.get_queue_attributes(
            QueueUrl=dlq_url, AttributeNames=["QueueArn"]
        )["Attributes"]["QueueArn"]
        print(f"  {OK}  Created DLQ: {SQS_DLQ}")
    except ClientError as e:
        if "QueueAlreadyExists" in str(e):
            resp = sqs.get_queue_url(QueueName=SQS_DLQ)
            dlq_url = resp["QueueUrl"]
            dlq_arn = sqs.get_queue_attributes(
                QueueUrl=dlq_url, AttributeNames=["QueueArn"]
            )["Attributes"]["QueueArn"]
            print(f"  {SKIP} DLQ already exists")
        else:
            raise

    # Main queue
    try:
        resp = sqs.create_queue(
            QueueName=SQS_QUEUE,
            Attributes={
                "VisibilityTimeout": "120",
                "MessageRetentionPeriod": "86400",
                "RedrivePolicy": json.dumps({
                    "deadLetterTargetArn": dlq_arn,
                    "maxReceiveCount": "3",
                }),
            },
        )
        queue_url = resp["QueueUrl"]
        print(f"  {OK}  Created queue: {SQS_QUEUE}")
    except ClientError as e:
        if "QueueAlreadyExists" in str(e):
            queue_url = sqs.get_queue_url(QueueName=SQS_QUEUE)["QueueUrl"]
            print(f"  {SKIP} Queue already exists")
        else:
            raise

    return queue_url


# ── Step 4: Secrets Manager ───────────────────────────────────────────────────

def setup_secrets() -> None:
    print("\n[4/8] Secrets Manager")
    sm = _client("secretsmanager")

    if not GROQ_KEY:
        print(f"  {FAIL} GROQ_API_KEY not set in .env — skipping")
        return

    try:
        sm.create_secret(Name=SECRET_NAME, SecretString=GROQ_KEY)
        print(f"  {OK}  Created secret: {SECRET_NAME}")
    except ClientError as e:
        if "ResourceExistsException" in str(e):
            sm.update_secret(SecretId=SECRET_NAME, SecretString=GROQ_KEY)
            print(f"  {OK}  Updated secret: {SECRET_NAME} (key refreshed)")
        else:
            raise


# ── Step 5: IAM Role for Lambda ───────────────────────────────────────────────

def setup_iam(account_id: str) -> str:
    print("\n[5/8] IAM role")
    iam = _client("iam")

    assume_policy = json.dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "lambda.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }],
    })

    # Create role
    try:
        resp = iam.create_role(
            RoleName=LAMBDA_ROLE,
            AssumeRolePolicyDocument=assume_policy,
            Description="CSM Lambda execution role",
            Tags=[{"Key": "Project", "Value": "csm"}],
        )
        role_arn = resp["Role"]["Arn"]
        print(f"  {OK}  Created IAM role: {LAMBDA_ROLE}")
    except ClientError as e:
        if "EntityAlreadyExists" in str(e):
            role_arn = iam.get_role(RoleName=LAMBDA_ROLE)["Role"]["Arn"]
            print(f"  {SKIP} IAM role already exists")
        else:
            raise

    # Attach managed policies
    managed = [
        "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
        "arn:aws:iam::aws:policy/AmazonDynamoDBReadOnlyAccess",
        "arn:aws:iam::aws:policy/CloudWatchFullAccess",
    ]
    for policy_arn in managed:
        try:
            iam.attach_role_policy(RoleName=LAMBDA_ROLE, PolicyArn=policy_arn)
        except ClientError:
            pass  # Already attached

    # Inline policy for Secrets Manager
    inline_policy = json.dumps({
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["secretsmanager:GetSecretValue"],
                "Resource": f"arn:aws:secretsmanager:{REGION}:{account_id}:secret:{SECRET_NAME}*",
            },
            {
                "Effect": "Allow",
                "Action": ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"],
                "Resource": f"arn:aws:sqs:{REGION}:{account_id}:{SQS_QUEUE}",
            },
        ],
    })
    iam.put_role_policy(
        RoleName=LAMBDA_ROLE,
        PolicyName="csm-extras",
        PolicyDocument=inline_policy,
    )
    print(f"  {OK}  IAM policies attached")
    return role_arn


# ── Step 6: Package & Deploy Lambda ───────────────────────────────────────────

def _build_lambda_zip() -> bytes:
    """Build the Lambda zip in memory — no temp files needed."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # Main handler
        alerter = (ROOT / "lambdas" / "staleness_alerter.py").read_bytes()
        zf.writestr("staleness_alerter.py", alerter)

        # Prompts directory
        prompts_dir = ROOT / "prompts"
        for prompt_file in prompts_dir.glob("*.txt"):
            zf.writestr(f"prompts/{prompt_file.name}", prompt_file.read_bytes())

    return buf.getvalue()


def setup_lambda(role_arn: str) -> str:
    print("\n[6/8] Lambda function")
    lam = _client("lambda")

    print("      Building Lambda package...")
    zip_bytes = _build_lambda_zip()
    print(f"      Package size: {len(zip_bytes) / 1024:.1f} KB")

    env_vars = {
        "DYNAMODB_REGISTRY_TABLE": REGISTRY_TABLE,
        "DYNAMODB_HISTORY_TABLE":  HISTORY_TABLE,
        "SLA_BREACH_MULTIPLIER":   "1.5",
        "GROQ_MODEL":              "llama-3.3-70b-versatile",
        "GROQ_MAX_TOKENS":         "300",
        "SLACK_WEBHOOK_URL":       SLACK_URL,
        "USE_LOCALSTACK":          "false",
        "AWS_DEFAULT_REGION":      REGION,
    }

    try:
        # Try to update first (if exists)
        lam.update_function_code(
            FunctionName=LAMBDA_NAME,
            ZipFile=zip_bytes,
        )
        lam.update_function_configuration(
            FunctionName=LAMBDA_NAME,
            Environment={"Variables": env_vars},
            Timeout=60,
            MemorySize=256,
        )
        resp = lam.get_function(FunctionName=LAMBDA_NAME)
        lambda_arn = resp["Configuration"]["FunctionArn"]
        print(f"  {OK}  Updated Lambda: {LAMBDA_NAME}")
    except ClientError as e:
        if "ResourceNotFoundException" in str(e):
            # Wait for IAM role to propagate (AWS needs ~10s)
            print("      Waiting 12s for IAM role propagation...")
            time.sleep(12)
            resp = lam.create_function(
                FunctionName=LAMBDA_NAME,
                Runtime="python3.11",
                Role=role_arn,
                Handler="staleness_alerter.handler",
                Code={"ZipFile": zip_bytes},
                Timeout=60,
                MemorySize=256,
                Environment={"Variables": env_vars},
                Tags={"Project": "csm"},
            )
            lambda_arn = resp["FunctionArn"]
            print(f"  {OK}  Created Lambda: {LAMBDA_NAME}")
        else:
            raise

    return lambda_arn


# ── Step 7: CloudWatch Events Rule ────────────────────────────────────────────

def setup_cloudwatch_events(lambda_arn: str, account_id: str) -> None:
    print("\n[7/8] CloudWatch Events (every 1 minute)")
    cw = _client("events")
    lam = _client("lambda")

    # Create/update rule
    resp = cw.put_rule(
        Name=CW_RULE_NAME,
        ScheduleExpression="rate(1 minute)",
        State="ENABLED",
        Description="Trigger CSM staleness alerter every 60 seconds",
    )
    rule_arn = resp["RuleArn"]

    # Add Lambda as target
    cw.put_targets(
        Rule=CW_RULE_NAME,
        Targets=[{
            "Id": "csm-staleness-alerter-target",
            "Arn": lambda_arn,
        }],
    )

    # Grant CloudWatch permission to invoke Lambda
    try:
        lam.add_permission(
            FunctionName=LAMBDA_NAME,
            StatementId="AllowCloudWatchEvents",
            Action="lambda:InvokeFunction",
            Principal="events.amazonaws.com",
            SourceArn=rule_arn,
        )
    except ClientError as e:
        if "ResourceConflictException" in str(e):
            pass  # Permission already exists

    print(f"  {OK}  EventBridge rule '{CW_RULE_NAME}' fires every 1 minute")
    print(f"  {OK}  Lambda target attached")


# ── Step 8: CloudWatch Dashboard ─────────────────────────────────────────────

def setup_dashboard() -> None:
    print("\n[8/8] CloudWatch Dashboard")
    cw = _client("cloudwatch")
    dashboard_body = {
        "widgets": [
            {
                "type": "metric",
                "x": 0, "y": 0, "width": 12, "height": 6,
                "properties": {
                    "title": "Cache Staleness (ms) — All Keys",
                    "metrics": [["CacheStalenessMonitor", "StalenessMs"]],
                    "period": 60,
                    "stat": "Maximum",
                    "view": "timeSeries",
                    "region": REGION,
                },
            },
            {
                "type": "metric",
                "x": 12, "y": 0, "width": 12, "height": 6,
                "properties": {
                    "title": "SLA Breach Percentage",
                    "metrics": [["CacheStalenessMonitor", "SLABreachPercent"]],
                    "period": 60,
                    "stat": "Maximum",
                    "view": "timeSeries",
                    "region": REGION,
                },
            },
            {
                "type": "metric",
                "x": 0, "y": 6, "width": 12, "height": 6,
                "properties": {
                    "title": "Lambda Invocations & Errors",
                    "metrics": [
                        ["AWS/Lambda", "Invocations", "FunctionName", LAMBDA_NAME],
                        ["AWS/Lambda", "Errors",      "FunctionName", LAMBDA_NAME],
                    ],
                    "period": 60,
                    "stat": "Sum",
                    "view": "timeSeries",
                    "region": REGION,
                },
            },
        ]
    }
    cw.put_dashboard(
        DashboardName="CacheStalenessMonitor",
        DashboardBody=json.dumps(dashboard_body),
    )
    print(f"  {OK}  Dashboard 'CacheStalenessMonitor' created")
    print(f"       https://{REGION}.console.aws.amazon.com/cloudwatch/home?region={REGION}#dashboards:name=CacheStalenessMonitor")


# ── Verify: Invoke Lambda once ────────────────────────────────────────────────

def verify_lambda() -> None:
    print("\n[VERIFY] Invoking Lambda once to confirm it works...")
    lam = _client("lambda")
    # Wait for Lambda to be active
    for _ in range(6):
        try:
            resp = lam.invoke(
                FunctionName=LAMBDA_NAME,
                InvocationType="RequestResponse",
                Payload=json.dumps({}).encode(),
            )
            payload = json.loads(resp["Payload"].read())
            status = payload.get("statusCode", "?")
            body   = json.loads(payload.get("body", "{}"))
            print(f"  {OK}  Lambda returned HTTP {status}")
            print(f"       violating_keys={body.get('violating_keys', 0)} | alerts_sent={body.get('alerts_sent', 0)}")
            return
        except ClientError as e:
            if "ResourceConflictException" in str(e):
                time.sleep(5)
                continue
            print(f"  {FAIL} Lambda invoke error: {e}")
            return
    print(f"  {FAIL} Lambda not ready after retries")


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 60)
    print("  Cache Staleness Monitor -- AWS Production Setup")
    print(f"  Region: {REGION}")
    print("=" * 60)

    if not ACCESS_KEY or not SECRET_KEY:
        print(f"\n{FAIL} AWS credentials not found!")
        print("   Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY in .env")
        sys.exit(1)

    try:
        account_id = _get_account_id()
        print(f"\nAWS Account: {account_id}")
    except Exception as e:
        print(f"\n{FAIL} Could not connect to AWS: {e}")
        print("   Check your credentials and internet connection.")
        sys.exit(1)

    try:
        setup_dynamodb()
        setup_s3()
        queue_url = setup_sqs()
        setup_secrets()
        role_arn  = setup_iam(account_id)
        lambda_arn = setup_lambda(role_arn)
        setup_cloudwatch_events(lambda_arn, account_id)
        setup_dashboard()
        verify_lambda()

        print("\n" + "=" * 60)
        print("  SETUP COMPLETE")
        print("=" * 60)
        print(f"\n  DynamoDB:   {REGISTRY_TABLE}, {HISTORY_TABLE}")
        print(f"  S3 bucket:  s3://{S3_BUCKET}")
        print(f"  SQS queue:  {queue_url}")
        print(f"  Lambda:     {LAMBDA_NAME} (triggers every 1 minute)")
        print(f"  Dashboard:  CacheStalenessMonitor in CloudWatch")
        print(f"\n  Slack alerts will fire to your webhook on SLA breaches.")
        print(f"  Next step: docker-compose up -d  (for Redis + PGVector)")
        print("=" * 60)

    except Exception as e:
        print(f"\n{FAIL} Setup failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
