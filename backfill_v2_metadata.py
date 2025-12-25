#!/usr/bin/env python3
"""
Backfill x402 v2 Bazaar Metadata Fields

This script re-fetches data from all facilitators and populates the new
v2 Bazaar metadata fields (example_input, example_output, input_schema_v2,
output_schema_v2, self_reported_category, self_reported_tags).

These fields are part of the x402 v2 Bazaar discovery extension, providing
richer metadata for service discovery and documentation.

Usage:
    python backfill_v2_metadata.py

Required environment variables:
    SUPABASE_URL: Supabase project URL
    SUPABASE_SERVICE_KEY: Supabase service role key

Prerequisites:
    Run the following SQL in Supabase first:

    ALTER TABLE resources ADD COLUMN IF NOT EXISTS example_input JSONB;
    ALTER TABLE resources ADD COLUMN IF NOT EXISTS example_output JSONB;
    ALTER TABLE resources ADD COLUMN IF NOT EXISTS input_schema_v2 JSONB;
    ALTER TABLE resources ADD COLUMN IF NOT EXISTS output_schema_v2 JSONB;
    ALTER TABLE resources ADD COLUMN IF NOT EXISTS self_reported_category TEXT;
    ALTER TABLE resources ADD COLUMN IF NOT EXISTS self_reported_tags TEXT[];
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


def extract_v2_metadata(item: dict) -> dict:
    """
    Extract x402 v2 Bazaar metadata fields from facilitator response.

    Returns dict with:
    - example_input: Example request data (from metadata.input)
    - example_output: Example response data (from metadata.output)
    - input_schema_v2: JSON Schema for input validation (from metadata.inputSchema)
    - output_schema_v2: JSON Schema for output (from metadata.outputSchema)
    - self_reported_category: Service category from Bazaar extension
    - self_reported_tags: Service tags array from Bazaar extension
    """
    metadata = item.get('metadata', {}) or {}

    return {
        'example_input': metadata.get('input'),
        'example_output': metadata.get('output'),
        'input_schema_v2': metadata.get('inputSchema'),
        'output_schema_v2': metadata.get('outputSchema'),
        'self_reported_category': item.get('category') or metadata.get('category'),
        'self_reported_tags': item.get('tags') or metadata.get('tags'),
    }


def backfill_v2_metadata(client: Client, items: list) -> dict:
    """Update existing resources with v2 Bazaar metadata fields"""
    stats = {
        'resources_updated': 0,
        'resources_with_v2_data': 0,
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

            # Extract v2 metadata
            v2_meta = extract_v2_metadata(item)

            # Build update data (only include non-null fields)
            update_data = {}

            if v2_meta.get('example_input'):
                update_data['example_input'] = json.dumps(v2_meta['example_input'])

            if v2_meta.get('example_output'):
                update_data['example_output'] = json.dumps(v2_meta['example_output'])

            if v2_meta.get('input_schema_v2'):
                update_data['input_schema_v2'] = json.dumps(v2_meta['input_schema_v2'])

            if v2_meta.get('output_schema_v2'):
                update_data['output_schema_v2'] = json.dumps(v2_meta['output_schema_v2'])

            if v2_meta.get('self_reported_category'):
                update_data['self_reported_category'] = v2_meta['self_reported_category']

            if v2_meta.get('self_reported_tags'):
                update_data['self_reported_tags'] = v2_meta['self_reported_tags']

            if update_data:
                client.table('resources').update(update_data).eq('id', resource_id).execute()
                stats['resources_updated'] += 1
                stats['resources_with_v2_data'] += 1
            else:
                stats['resources_updated'] += 1  # Checked but no v2 data

        except Exception as e:
            print(f"  Error processing {resource_url}: {e}")
            stats['errors'] += 1

    return stats


def main():
    print("=" * 60)
    print(f"Backfill v2 Bazaar Metadata - {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)
    print()
    print("This script populates x402 v2 Bazaar metadata fields:")
    print("  - example_input: Example request data")
    print("  - example_output: Example response data")
    print("  - input_schema_v2: JSON Schema for input validation")
    print("  - output_schema_v2: JSON Schema for output")
    print("  - self_reported_category: Service category")
    print("  - self_reported_tags: Service tags array")
    print()

    # Connect to Supabase
    try:
        client = get_supabase_client()
        print("Connected to Supabase")
    except Exception as e:
        print(f"Failed to connect to Supabase: {e}")
        return

    total_stats = {
        'resources_updated': 0,
        'resources_with_v2_data': 0,
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
        stats = backfill_v2_metadata(client, items)

        for key in total_stats:
            total_stats[key] += stats[key]

        print(f"  {name}: {stats['resources_updated']} checked, {stats['resources_with_v2_data']} with v2 data")

    # Summary
    print("\n" + "=" * 60)
    print("Backfill Complete!")
    print(f"  Resources checked: {total_stats['resources_updated']}")
    print(f"  With v2 metadata: {total_stats['resources_with_v2_data']}")
    print(f"  Not found (new resources): {total_stats['not_found']}")
    print(f"  Errors: {total_stats['errors']}")
    print("=" * 60)


if __name__ == "__main__":
    main()
