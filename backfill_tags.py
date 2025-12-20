#!/usr/bin/env python3
"""
Backfill Tags with New Categories
Re-tags all resources using the new category system.

Categories:
- ai_agent: AI agents, swarms, autonomous workflows
- llm_inference: LLM/GPT inference services
- blockchain_data: On-chain data, token info, DEX data
- trading: Trading, swaps, market data
- nft: NFT-related services
- payment: Payment services, USDC transfers
- social_media: Twitter/social analytics
- developer_tools: APIs, SDKs, utilities
- content: Media, articles, images
- security: Risk/compliance services
- other: Uncategorized

Usage:
    python backfill_tags.py
"""

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

# Tag keywords (same as in fetch_discovery.py)
TAG_KEYWORDS = {
    'ai_agent': [
        'agent', 'swarm', 'autonomous', 'workflow', 'assistant', 'bot',
        'eliza', 'virtuals', 'daydreams', 'ai-agent', 'aiagent'
    ],
    'llm_inference': [
        'llm', 'gpt', 'claude', 'gemini', 'inference', 'completion', 'chat',
        'openai', 'anthropic', 'mistral', 'llama', 'generate'
    ],
    'blockchain_data': [
        'onchain', 'on-chain', 'token-info', 'dex-data', 'chain-data',
        'blockchain', 'transaction', 'block', 'address', 'balance'
    ],
    'trading': [
        'trade', 'trading', 'swap', 'dex', 'exchange', 'market', 'price',
        'order', 'quote', 'liquidity'
    ],
    'nft': [
        'nft', 'collectible', 'mint', 'metadata', 'opensea', 'collection'
    ],
    'payment': [
        'payment', 'pay', 'transfer', 'usdc', 'send', 'receive', 'wallet'
    ],
    'social_media': [
        'twitter', 'social', 'tweet', 'post', 'farcaster', 'lens', 'x.com',
        'analytics', 'follower'
    ],
    'developer_tools': [
        'sdk', 'api', 'developer', 'tool', 'utility', 'webhook', 'rpc',
        'endpoint', 'integration'
    ],
    'content': [
        'media', 'image', 'video', 'article', 'content', 'generate-image',
        'text-to', 'image-to'
    ],
    'security': [
        'security', 'risk', 'compliance', 'audit', 'verify', 'kyc', 'aml'
    ],
}


def detect_tags(resource_url: str, description: str = '') -> list:
    """Detect tags based on URL and description keywords"""
    text = f"{resource_url} {description}".lower()
    tags = []

    for tag_name, keywords in TAG_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            tags.append(tag_name)

    # Default to 'other' if no tags detected
    if not tags:
        tags = ['other']

    return tags


def main():
    print("=" * 60)
    print("Backfill Tags with New Categories")
    print("=" * 60)

    # Connect to Supabase
    url = os.environ.get('SUPABASE_URL')
    key = os.environ.get('SUPABASE_SERVICE_KEY')

    if not url or not key:
        print("Error: SUPABASE_URL or SUPABASE_SERVICE_KEY not set")
        return

    supabase = create_client(url, key)

    # Get all tags
    print("\nFetching tag mappings...")
    result = supabase.table('tags').select('id, name').execute()
    tag_map = {t['name']: t['id'] for t in result.data}
    print(f"Found {len(tag_map)} tags")

    # Get all resources with pagination
    print("\nFetching resources...")
    resources = []
    offset = 0
    limit = 1000
    while True:
        result = supabase.table('resources').select('id, resource, description').range(offset, offset + limit - 1).execute()
        resources.extend(result.data)
        if len(result.data) < limit:
            break
        offset += limit
    print(f"Found {len(resources)} resources")

    # NOTE: We only ADD new tags, never delete existing ones
    print("\nAdding new category tags to resources (preserving existing)...")

    # Tag each resource (using upsert to avoid duplicates)
    print("\nTagging resources with new categories...")
    tag_counts = {name: 0 for name in list(TAG_KEYWORDS.keys()) + ['other']}

    for i, resource in enumerate(resources):
        resource_url = resource.get('resource', '')
        description = resource.get('description', '') or ''

        tags = detect_tags(resource_url, description)

        for tag_name in tags:
            tag_id = tag_map.get(tag_name)
            if tag_id:
                try:
                    # Use upsert to safely add without duplicates
                    supabase.table('resource_tags').upsert(
                        {'resource_id': resource['id'], 'tag_id': tag_id},
                        on_conflict='resource_id,tag_id'
                    ).execute()
                    tag_counts[tag_name] += 1
                except Exception as e:
                    # Just skip if there's an issue
                    pass

        if (i + 1) % 100 == 0:
            print(f"  Processed {i + 1}/{len(resources)} resources...")

    # Print summary
    print("\n" + "=" * 60)
    print("Tag Distribution:")
    print("=" * 60)
    print(f"{'Category':<20} {'Count':>6}")
    print("-" * 30)

    for tag_name in sorted(tag_counts.keys(), key=lambda x: tag_counts[x], reverse=True):
        count = tag_counts[tag_name]
        if count > 0:
            print(f"{tag_name:<20} {count:>6}")

    print("=" * 60)
    print(f"Total: {sum(tag_counts.values())} tags applied")


if __name__ == "__main__":
    main()
