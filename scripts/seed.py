"""Create the local items table if it is missing and load seed/items.json (for DynamoDB Local)."""

import json
import os
import sys
import time
from decimal import Decimal
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError

SEED_FILE = Path(__file__).resolve().parent.parent / "seed" / "items.json"


def load_items(path=SEED_FILE):
    items = json.loads(Path(path).read_text(encoding="utf-8"))
    return [
        {
            "id": str(item["id"]),
            "name": item["name"],
            "price": Decimal(str(item["price"])),
            "stock": int(item["stock"]),
        }
        for item in items
    ]


def main():
    endpoint = os.environ.get("DYNAMODB_ENDPOINT", "http://localhost:8000")
    table_name = os.environ.get("TABLE_NAME", "inventory-items-local")
    options = {
        "endpoint_url": endpoint,
        "region_name": os.environ.get("AWS_REGION", "ap-southeast-2"),
        "aws_access_key_id": os.environ.get("AWS_ACCESS_KEY_ID", "local"),
        "aws_secret_access_key": os.environ.get("AWS_SECRET_ACCESS_KEY", "local"),
    }
    client = boto3.client("dynamodb", **options)

    for attempt in range(30):
        try:
            existing = client.list_tables()["TableNames"]
            break
        except (BotoCoreError, ClientError):
            if attempt == 29:
                print(f"DynamoDB is not reachable at {endpoint}", file=sys.stderr)
                return 1
            time.sleep(1)

    if table_name not in existing:
        client.create_table(
            TableName=table_name,
            AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
            KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
            BillingMode="PAY_PER_REQUEST",
        )
        client.get_waiter("table_exists").wait(TableName=table_name)

    table = boto3.resource("dynamodb", **options).Table(table_name)
    items = load_items()
    with table.batch_writer() as batch:
        for item in items:
            batch.put_item(Item=item)
    print(f"Seeded {len(items)} items into {table_name} at {endpoint}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
