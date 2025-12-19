#!/usr/bin/env python3
"""
x402 Discovery Data Fetcher
每小时拉取所有 facilitator 的服务发现数据，存到 GCS
"""

import json
import urllib.request
import urllib.error
from datetime import datetime, timezone
import time
import os

# 6 个 Facilitator Discovery Endpoints
FACILITATORS = {
    "cdp_coinbase": "https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources",
    "payai": "https://facilitator.payai.network/discovery/resources",
    "questflow": "https://facilitator.questflow.ai/discovery/resources",
    "anyspend": "https://mainnet.anyspend.com/x402/discovery/resources",
    "aurracloud": "https://x402-facilitator.aurracloud.com/discovery/resources",
    "thirdweb": "https://api.thirdweb.com/v1/payments/x402/discovery/resources",
}

def fetch_with_pagination(url, facilitator_name, limit=100, max_retries=3):
    """分页拉取数据，处理 rate limit"""
    all_items = []
    offset = 0

    while True:
        paginated_url = f"{url}?offset={offset}&limit={limit}"

        for retry in range(max_retries):
            try:
                req = urllib.request.Request(paginated_url, headers={
                    'User-Agent': 'BlockRun/1.0',
                    'Accept': 'application/json'
                })
                with urllib.request.urlopen(req, timeout=30) as response:
                    data = json.loads(response.read().decode())

                    # 处理不同的响应格式
                    if isinstance(data, list):
                        items = data
                    elif isinstance(data, dict):
                        items = data.get('items', data.get('resources', []))
                    else:
                        items = []

                    if not items:
                        return all_items

                    all_items.extend(items)
                    print(f"  {facilitator_name}: fetched {len(all_items)} items...")

                    # 如果返回的少于 limit，说明没有更多了
                    if len(items) < limit:
                        return all_items

                    offset += limit
                    time.sleep(0.5)  # 避免 rate limit
                    break

            except urllib.error.HTTPError as e:
                if e.code == 429:  # Rate limited
                    wait_time = 2 ** (retry + 2)
                    print(f"  Rate limited, waiting {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    print(f"  HTTP Error {e.code}: {e.reason}")
                    if retry == max_retries - 1:
                        return all_items
            except Exception as e:
                print(f"  Error: {e}")
                if retry == max_retries - 1:
                    return all_items
                time.sleep(1)

    return all_items


def fetch_all_discovery():
    """拉取所有 facilitator 的数据"""
    result = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "facilitators": {}
    }

    for name, url in FACILITATORS.items():
        print(f"Fetching {name}...")
        try:
            items = fetch_with_pagination(url, name)
            result["facilitators"][name] = {
                "url": url,
                "count": len(items),
                "items": items
            }
            print(f"  ✓ {name}: {len(items)} items")
        except Exception as e:
            print(f"  ✗ {name}: {e}")
            result["facilitators"][name] = {
                "url": url,
                "count": 0,
                "items": [],
                "error": str(e)
            }

    return result


def save_local(data, output_dir="data"):
    """保存到本地"""
    os.makedirs(output_dir, exist_ok=True)

    timestamp = datetime.now(timezone.utc)
    filename = f"discovery_{timestamp.strftime('%Y-%m-%d_%H')}.json"
    filepath = os.path.join(output_dir, filename)

    with open(filepath, 'w') as f:
        json.dump(data, f)

    print(f"\nSaved to {filepath}")
    return filepath, filename


def upload_to_gcs(filepath, filename, bucket_name="blockrun-data"):
    """上传到 Google Cloud Storage"""
    try:
        from google.cloud import storage

        client = storage.Client()
        bucket = client.bucket(bucket_name)

        blob_name = f"discovery/{filename}"
        blob = bucket.blob(blob_name)
        blob.upload_from_filename(filepath)

        print(f"Uploaded to gs://{bucket_name}/{blob_name}")
        return True
    except ImportError:
        print("google-cloud-storage not installed, skipping GCS upload")
        return False
    except Exception as e:
        print(f"GCS upload failed: {e}")
        return False


def main():
    print("=" * 60)
    print(f"x402 Discovery Fetch - {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)

    # 拉取数据
    data = fetch_all_discovery()

    # 统计
    total_items = sum(f["count"] for f in data["facilitators"].values())
    print(f"\nTotal: {total_items} items from {len(FACILITATORS)} facilitators")

    # 保存本地
    filepath, filename = save_local(data)

    # 上传 GCS (如果配置了)
    bucket = os.environ.get("GCS_BUCKET", "blockrun-data")
    if os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCS_BUCKET"):
        upload_to_gcs(filepath, filename, bucket)

    print("\nDone!")


if __name__ == "__main__":
    main()
