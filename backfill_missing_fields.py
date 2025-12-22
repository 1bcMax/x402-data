#!/usr/bin/env python3
"""
Backfill Missing Fields from Facilitator APIs

This script re-fetches data from all facilitators and updates existing records
with the newly captured fields (mimeType, channel, discoverable, outputSchema, etc.)

Usage:
    python backfill_missing_fields.py

Required environment variables:
    SUPABASE_URL: Supabase project URL
    SUPABASE_SERVICE_KEY: Supabase service role key
"""

import json
import urllib.request
import urllib.error
from datetime import datetime, timezone
import time
import os

try:
    from supabase import create_client, Client
except ImportError:
    print("Error: supabase not installed. Run: pip install supabase")
    exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# Facilitator endpoints
FACILITATORS = {
    "cdp_coinbase": "https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources",
    "payai": "https://facilitator.payai.network/discovery/resources",
    "questflow": "https://facilitator.questflow.ai/discovery/resources",
    "anyspend": "https://mainnet.anyspend.com/x402/discovery/resources",
    "aurracloud": "https://x402-facilitator.aurracloud.com/discovery/resources",
    "thirdweb": "https://api.thirdweb.com/v1/payments/x402/discovery/resources",
}


def get_supabase_client() -> Client:
    """Initialize Supabase client from environment variables"""
    url = os.environ.get('SUPABASE_URL')
    key = os.environ.get('SUPABASE_SERVICE_KEY')

    if not url or not key:
        raise ValueError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set")

    return create_client(url, key)


def fetch_with_pagination(url: str, name: str, limit: int = 100) -> list:
    """Fetch data with pagination"""
    all_items = []
    offset = 0

    while True:
        paginated_url = f"{url}?offset={offset}&limit={limit}"

        try:
            req = urllib.request.Request(paginated_url, headers={
                'User-Agent': 'BlockRun/1.0',
                'Accept': 'application/json'
            })
            with urllib.request.urlopen(req, timeout=30) as response:
                data = json.loads(response.read().decode())

                # Handle different response formats
                if isinstance(data, list):
                    items = data
                elif isinstance(data, dict):
                    items = data.get('items', data.get('resources', []))
                    if not items and 'data' in data and isinstance(data['data'], dict):
                        items = data['data'].get('items', [])
                else:
                    items = []

                if not items:
                    break

                all_items.extend(items)
                print(f"  {name}: fetched {len(all_items)} items")

                if len(items) < limit:
                    break

                offset += limit
                time.sleep(0.5)

        except Exception as e:
            print(f"  {name}: error - {e}")
            break

    return all_items


def backfill_resources(client: Client, items: list) -> dict:
    """Update existing resources with new fields"""
    stats = {
        'resources_updated': 0,
        'accepts_updated': 0,
        'not_found': 0,
        'errors': 0,
    }

    for item in items:
        resource_url = item.get('resource', '')
        if not resource_url:
            continue

        try:
            # Find existing resource
            result = client.table('resources').select('id').eq('resource', resource_url).execute()

            if not result.data:
                stats['not_found'] += 1
                continue

            resource_id = result.data[0]['id']

            # Update resource with new fields
            update_data = {}
            if item.get('method'):
                update_data['method'] = item['method']
            if item.get('metadata'):
                update_data['metadata'] = json.dumps(item['metadata'])
            if item.get('inputSchema'):
                update_data['input_schema'] = json.dumps(item['inputSchema'])
            if item.get('outputSchema'):
                update_data['item_output_schema'] = json.dumps(item['outputSchema'])

            if update_data:
                client.table('resources').update(update_data).eq('id', resource_id).execute()
                stats['resources_updated'] += 1

            # Update accepts with new fields
            for accept in item.get('accepts', []):
                output_schema = accept.get('outputSchema', {}) or {}
                input_schema = output_schema.get('input', {}) or {}
                extra = accept.get('extra', {}) or {}

                accept_update = {}
                if output_schema:
                    accept_update['output_schema'] = json.dumps(output_schema)
                if extra:
                    accept_update['extra'] = json.dumps(extra)
                if accept.get('mimeType'):
                    accept_update['mime_type'] = accept['mimeType']

                channel = accept.get('channel') or extra.get('channel')
                if channel:
                    accept_update['channel'] = channel

                discoverable = input_schema.get('discoverable')
                if discoverable is not None:
                    accept_update['discoverable'] = discoverable

                if accept_update:
                    network = accept.get('network', '')
                    try:
                        client.table('accepts').update(accept_update).eq('resource_id', resource_id).eq('network', network).execute()
                        stats['accepts_updated'] += 1
                    except Exception as e:
                        print(f"    Accept update error for {resource_url}: {e}")
                        stats['errors'] += 1

        except Exception as e:
            print(f"  Error processing {resource_url}: {e}")
            stats['errors'] += 1

    return stats


def main():
    print("=" * 60)
    print(f"Backfill Missing Fields - {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)

    # Connect to Supabase
    try:
        client = get_supabase_client()
        print("Connected to Supabase")
    except Exception as e:
        print(f"Failed to connect to Supabase: {e}")
        return

    total_stats = {
        'resources_updated': 0,
        'accepts_updated': 0,
        'not_found': 0,
        'errors': 0,
    }

    # Fetch from all facilitators and update
    for name, url in FACILITATORS.items():
        print(f"\nProcessing {name}...")

        items = fetch_with_pagination(url, name)
        if not items:
            print(f"  No items fetched from {name}")
            continue

        print(f"  Updating {len(items)} items...")
        stats = backfill_resources(client, items)

        for key in total_stats:
            total_stats[key] += stats[key]

        print(f"  {name}: {stats['resources_updated']} resources, {stats['accepts_updated']} accepts updated")

    # Summary
    print("\n" + "=" * 60)
    print("Backfill Complete!")
    print(f"  Resources updated: {total_stats['resources_updated']}")
    print(f"  Accepts updated: {total_stats['accepts_updated']}")
    print(f"  Not found (new resources): {total_stats['not_found']}")
    print(f"  Errors: {total_stats['errors']}")
    print("=" * 60)


if __name__ == "__main__":
    main()
