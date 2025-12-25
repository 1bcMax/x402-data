#!/usr/bin/env python3
"""
x402 Discovery Data Pipeline
Hourly job that:
1. Fetches from all 6 facilitators
2. Filters testnet networks
3. Deduplicates by resource URL
4. Upserts to Supabase (origins -> resources -> accepts)
5. Scrapes metadata for new origins

Environment variables required:
- SUPABASE_URL: Supabase project URL
- SUPABASE_SERVICE_KEY: Supabase service role key (for bypassing RLS)
"""

import json
import urllib.request
import urllib.error
from urllib.parse import urlparse
from datetime import datetime, timezone
import time
import os
import re
from typing import Optional, List, Dict, Any, Set

# Optional imports with fallbacks
try:
    from supabase import create_client, Client
    HAS_SUPABASE = True
except ImportError:
    HAS_SUPABASE = False
    print("Warning: supabase not installed, will save locally only")

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False
    print("Warning: beautifulsoup4 not installed, scraping disabled")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ============================================
# CONFIGURATION
# ============================================

FACILITATORS = {
    "cdp_coinbase": "https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources",
    "payai": "https://facilitator.payai.network/discovery/resources",
    "questflow": "https://facilitator.questflow.ai/discovery/resources",
    "anyspend": "https://mainnet.anyspend.com/x402/discovery/resources",
    "aurracloud": "https://x402-facilitator.aurracloud.com/discovery/resources",
    "thirdweb": "https://api.thirdweb.com/v1/payments/x402/discovery/resources",
}

# Testnet patterns to filter out
TESTNET_PATTERNS = [
    '-sepolia', '-testnet', 'goerli', 'mumbai',
    'holesky', '-devnet', 'sepolia', 'testnet'
]

# Hosting platform domains to filter out (not serious projects)
HOSTING_DOMAINS = [
    '.vercel.app',
    '.netlify.app',
    '.render.com',
    '.onrender.com',
    '.herokuapp.com',
    '.railway.app',
    '.fly.dev',
    '.replit.dev',
    '.glitch.me',
    '.surge.sh',
    '.pages.dev',      # Cloudflare Pages
    '.workers.dev',    # Cloudflare Workers
    '.nx.link',        # Some hosting
    'localhost',
]

# Auto-tagging keywords (new categories)
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

# USDC token addresses by network
USDC_ADDRESSES = {
    'base': '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913',
    'solana': 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v',
}

# Traction sync API keys (from environment)
ALCHEMY_API_KEY = os.environ.get('ALCHEMY_API_KEY')
HELIUS_API_KEY = os.environ.get('HELIUS_API_KEY')

# ============================================
# SUPABASE CLIENT
# ============================================

def get_supabase_client() -> Optional['Client']:
    """Initialize Supabase client from environment variables"""
    if not HAS_SUPABASE:
        return None

    url = os.environ.get('SUPABASE_URL')
    key = os.environ.get('SUPABASE_SERVICE_KEY')

    if not url or not key:
        print("Warning: SUPABASE_URL or SUPABASE_SERVICE_KEY not set")
        return None

    return create_client(url, key)

# ============================================
# TESTNET FILTERING
# ============================================

def is_testnet(network: str) -> bool:
    """Check if a network name indicates a testnet"""
    if not network:
        return False
    network_lower = network.lower()
    return any(pattern in network_lower for pattern in TESTNET_PATTERNS)

def is_hosting_domain(domain: str) -> bool:
    """Check if domain is a hosting platform (not a serious project)"""
    if not domain:
        return True
    domain_lower = domain.lower()
    return any(host in domain_lower for host in HOSTING_DOMAINS)

def get_root_domain(domain: str) -> str:
    """
    Extract root domain for scraping.
    e.g., data-x402.hexens.io -> hexens.io
          api.lucyos.ai -> lucyos.ai
          sub.domain.example.com -> example.com
    """
    if not domain:
        return domain

    parts = domain.split('.')

    # Handle special TLDs like .co.uk, .com.au
    special_tlds = ['co.uk', 'com.au', 'co.nz', 'co.jp', 'com.br']
    domain_lower = domain.lower()

    for tld in special_tlds:
        if domain_lower.endswith('.' + tld):
            # Get the part before the special TLD
            prefix = domain_lower[:-len(tld)-1]
            if '.' in prefix:
                return prefix.split('.')[-1] + '.' + tld
            return domain

    # For normal domains, return last 2 parts
    if len(parts) >= 2:
        return '.'.join(parts[-2:])

    return domain

def filter_accepts(accepts: list) -> list:
    """Filter out testnet payment options from accepts list"""
    return [a for a in accepts if not is_testnet(a.get('network', ''))]

# ============================================
# DEDUPLICATION
# ============================================

def deduplicate_resources(all_items: list) -> list:
    """
    Deduplicate resources by URL, keeping the first occurrence.
    Track newest lastUpdated for each resource.
    """
    seen = {}  # resource_url -> item

    for item in all_items:
        resource_url = item.get('resource', '')
        if not resource_url:
            continue

        if resource_url not in seen:
            seen[resource_url] = item
        else:
            # Keep the one with newer lastUpdated
            existing = seen[resource_url]
            existing_date = existing.get('lastUpdated', '')
            new_date = item.get('lastUpdated', '')
            if new_date > existing_date:
                seen[resource_url] = item

    return list(seen.values())

# ============================================
# AUTO-TAGGING
# ============================================

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


def extract_v2_metadata(item: dict) -> dict:
    """
    Extract x402 v2 Bazaar metadata fields from facilitator response.

    These fields are part of the Bazaar discovery extension in x402 v2,
    providing richer metadata for service discovery and documentation.

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

# ============================================
# ORIGIN METADATA SCRAPER
# ============================================

def scrape_origin_metadata(domain: str) -> dict:
    """
    Scrape metadata from origin domain.
    Returns dict with: title, description, favicon, og_image, twitter, discord, github
    """
    if not HAS_BS4:
        return {}

    metadata = {
        'title': None,
        'description': None,
        'favicon': None,
        'og_image': None,
        'twitter': None,
        'discord': None,
        'github': None,
    }

    try:
        url = f"https://{domain}"
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml',
        })

        with urllib.request.urlopen(req, timeout=10) as response:
            html = response.read().decode('utf-8', errors='ignore')

        soup = BeautifulSoup(html, 'lxml')

        # Title
        if soup.title:
            metadata['title'] = soup.title.string[:200] if soup.title.string else None
        og_title = soup.find('meta', property='og:title')
        if og_title and og_title.get('content'):
            metadata['title'] = og_title['content'][:200]

        # Description
        meta_desc = soup.find('meta', attrs={'name': 'description'})
        if meta_desc and meta_desc.get('content'):
            metadata['description'] = meta_desc['content'][:500]
        og_desc = soup.find('meta', property='og:description')
        if og_desc and og_desc.get('content'):
            metadata['description'] = og_desc['content'][:500]

        # Favicon
        favicon = soup.find('link', rel=lambda x: x and 'icon' in x.lower() if x else False)
        if favicon and favicon.get('href'):
            href = favicon['href']
            if href.startswith('//'):
                metadata['favicon'] = f"https:{href}"
            elif href.startswith('/'):
                metadata['favicon'] = f"https://{domain}{href}"
            elif href.startswith('http'):
                metadata['favicon'] = href
            else:
                metadata['favicon'] = f"https://{domain}/{href}"

        # OG Image
        og_image = soup.find('meta', property='og:image')
        if og_image and og_image.get('content'):
            metadata['og_image'] = og_image['content']

        # Social links - search all anchor tags
        for a in soup.find_all('a', href=True):
            href = a['href'].lower()
            if 'twitter.com/' in href or 'x.com/' in href:
                # Extract handle or full URL
                match = re.search(r'(?:twitter\.com|x\.com)/([^/?]+)', href)
                if match and match.group(1) not in ['share', 'intent', 'home']:
                    metadata['twitter'] = match.group(1)
            elif 'discord.gg/' in href or 'discord.com/' in href:
                metadata['discord'] = a['href']
            elif 'github.com/' in href:
                match = re.search(r'github\.com/([^/?]+)', href)
                if match:
                    metadata['github'] = match.group(1)

        print(f"    Scraped {domain}: title={metadata['title'][:30] if metadata['title'] else None}...")

    except Exception as e:
        print(f"    Failed to scrape {domain}: {e}")

    return metadata

# ============================================
# DATA FETCHING
# ============================================

def fetch_with_pagination(url: str, facilitator_name: str, limit: int = 100, max_retries: int = 3) -> list:
    """Fetch data with pagination and rate limit handling"""
    all_items = []
    offset = 0
    hosting_filtered = 0
    testnet_filtered = 0

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
                        return all_items

                    # Filter testnet accepts and hosting domains from each item
                    for item in items:
                        # Skip hosting platform domains (not serious projects)
                        resource_url = item.get('resource', '')
                        if resource_url:
                            parsed = urlparse(resource_url)
                            if is_hosting_domain(parsed.netloc):
                                hosting_filtered += 1
                                continue

                        if 'accepts' in item and item['accepts']:
                            item['accepts'] = filter_accepts(item['accepts'])
                            # Skip items with no mainnet payment options after filtering
                            if not item['accepts']:
                                testnet_filtered += 1
                                continue
                        elif not item.get('accepts'):
                            # Skip items without any payment options
                            testnet_filtered += 1
                            continue
                        all_items.append(item)

                    print(f"  {facilitator_name}: fetched {len(all_items)} items (filtered: {hosting_filtered} hosting, {testnet_filtered} testnet)")

                    if len(items) < limit:
                        return all_items

                    offset += limit
                    time.sleep(0.5)
                    break

            except urllib.error.HTTPError as e:
                if e.code == 429:
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


def fetch_all_discovery() -> list:
    """Fetch from all facilitators and return combined list"""
    all_items = []

    for name, url in FACILITATORS.items():
        print(f"Fetching {name}...")
        try:
            items = fetch_with_pagination(url, name)
            all_items.extend(items)
            print(f"  {name}: {len(items)} items (mainnet only)")
        except Exception as e:
            print(f"  {name}: error - {e}")

    return all_items

# ============================================
# SUPABASE UPSERT
# ============================================

def upsert_to_supabase(client: 'Client', items: list) -> tuple:
    """
    Upsert items to Supabase database.
    Returns (new_origins, stats_dict)
    """
    stats = {
        'new_origins': 0,
        'updated_origins': 0,
        'new_resources': 0,
        'updated_resources': 0,
        'new_accepts': 0,
        'errors': 0,
    }
    new_origin_domains = []

    # Get existing origins for comparison
    existing_origins = {}
    try:
        result = client.table('origins').select('id, domain').execute()
        existing_origins = {o['domain']: o['id'] for o in result.data}
    except Exception as e:
        print(f"Error fetching existing origins: {e}")

    # Get existing tags
    tag_map = {}
    try:
        result = client.table('tags').select('id, name').execute()
        tag_map = {t['name']: t['id'] for t in result.data}
    except Exception as e:
        print(f"Error fetching tags: {e}")

    # Process each item
    for item in items:
        try:
            resource_url = item.get('resource', '')
            if not resource_url:
                continue

            # Parse origin from resource URL
            parsed = urlparse(resource_url)
            origin = f"{parsed.scheme}://{parsed.netloc}"
            domain = parsed.netloc
            path = parsed.path or '/'

            # 1. Upsert origin
            origin_id = existing_origins.get(domain)
            if not origin_id:
                # New origin - use upsert to handle race conditions
                origin_data = {
                    'origin': origin,
                    'domain': domain,
                    'resource_count': 1,
                }
                try:
                    result = client.table('origins').upsert(
                        origin_data,
                        on_conflict='origin'
                    ).execute()
                    if result.data:
                        origin_id = result.data[0]['id']
                        existing_origins[domain] = origin_id
                        # Check if this was a new insert vs update
                        if result.data[0].get('created_at') == result.data[0].get('updated_at'):
                            new_origin_domains.append(domain)
                            stats['new_origins'] += 1
                        else:
                            stats['updated_origins'] += 1
                except Exception as e:
                    # If upsert fails, try to fetch existing
                    try:
                        existing = client.table('origins').select('id').eq('domain', domain).single().execute()
                        if existing.data:
                            origin_id = existing.data['id']
                            existing_origins[domain] = origin_id
                            stats['updated_origins'] += 1
                    except:
                        pass
            else:
                stats['updated_origins'] += 1

            if not origin_id:
                stats['errors'] += 1
                continue

            # 2. Upsert resource
            # Extract v2 Bazaar metadata
            v2_meta = extract_v2_metadata(item)

            resource_data = {
                'origin_id': origin_id,
                'resource': resource_url,
                'path': path,
                'type': item.get('type', 'http'),
                'x402_version': item.get('x402Version', 1),
                'method': item.get('method', 'POST'),  # Read from data, fallback to POST
                'last_updated': item.get('lastUpdated'),
                # Legacy fields from facilitator API (item level)
                'metadata': json.dumps(item.get('metadata')) if item.get('metadata') else None,
                'input_schema': json.dumps(item.get('inputSchema')) if item.get('inputSchema') else None,
                'item_output_schema': json.dumps(item.get('outputSchema')) if item.get('outputSchema') else None,
                # x402 v2 Bazaar metadata fields
                'example_input': json.dumps(v2_meta['example_input']) if v2_meta.get('example_input') else None,
                'example_output': json.dumps(v2_meta['example_output']) if v2_meta.get('example_output') else None,
                'input_schema_v2': json.dumps(v2_meta['input_schema_v2']) if v2_meta.get('input_schema_v2') else None,
                'output_schema_v2': json.dumps(v2_meta['output_schema_v2']) if v2_meta.get('output_schema_v2') else None,
                'self_reported_category': v2_meta.get('self_reported_category'),
                'self_reported_tags': v2_meta.get('self_reported_tags'),  # Already an array
            }

            # Check for description (priority: item metadata > first accept)
            accepts = item.get('accepts', [])
            item_metadata = item.get('metadata', {}) or {}
            if item_metadata.get('description'):
                resource_data['description'] = item_metadata['description'][:500]
            elif accepts and accepts[0].get('description'):
                resource_data['description'] = accepts[0]['description'][:500]

            result = client.table('resources').upsert(
                resource_data,
                on_conflict='resource'
            ).execute()

            if not result.data:
                stats['errors'] += 1
                continue

            resource_id = result.data[0]['id']
            stats['new_resources'] += 1

            # 3. Upsert accepts
            for accept in accepts:
                # Determine asset name from extra.name or by checking known addresses
                asset_name = None
                extra = accept.get('extra', {}) or {}
                if extra.get('name'):
                    asset_name = extra['name']
                else:
                    asset_lower = accept.get('asset', '').lower()
                    if 'usdc' in asset_lower or accept.get('asset', '') in USDC_ADDRESSES.values():
                        asset_name = 'USDC'

                # Extract outputSchema fields for queryability
                output_schema = accept.get('outputSchema', {}) or {}
                input_schema = output_schema.get('input', {}) or {}

                accept_data = {
                    'resource_id': resource_id,
                    'scheme': accept.get('scheme', 'exact'),
                    'network': accept.get('network', ''),
                    'asset': accept.get('asset', ''),
                    'asset_name': asset_name,
                    'pay_to': accept.get('payTo', ''),
                    'max_amount_required': accept.get('maxAmountRequired', '0'),
                    'max_timeout_seconds': accept.get('maxTimeoutSeconds', 300),
                    'output_schema': json.dumps(output_schema) if output_schema else None,
                    'extra': json.dumps(extra) if extra else None,
                    # New fields from facilitator API
                    'mime_type': accept.get('mimeType'),
                    'channel': accept.get('channel') or extra.get('channel'),
                    'discoverable': input_schema.get('discoverable', True),
                }

                # Calculate price in USD (USDC has 6 decimals)
                try:
                    amount = int(accept.get('maxAmountRequired', '0'))
                    accept_data['price_usd'] = amount / 1_000_000
                except:
                    pass

                try:
                    client.table('accepts').upsert(
                        accept_data,
                        on_conflict='resource_id,scheme,network'
                    ).execute()
                    stats['new_accepts'] += 1
                except Exception as e:
                    print(f"    Accept upsert error: {e}")
                    stats['errors'] += 1

            # 4. Auto-tag resource
            description = resource_data.get('description', '')
            detected_tags = detect_tags(resource_url, description)

            for tag_name in detected_tags:
                tag_id = tag_map.get(tag_name)
                if tag_id:
                    try:
                        client.table('resource_tags').upsert(
                            {'resource_id': resource_id, 'tag_id': tag_id},
                            on_conflict='resource_id,tag_id'
                        ).execute()
                    except:
                        pass

        except Exception as e:
            print(f"  Error processing {item.get('resource', 'unknown')}: {e}")
            stats['errors'] += 1

    return new_origin_domains, stats


def update_origin_metadata(client: 'Client', domain: str, metadata: dict):
    """Update origin with scraped metadata"""
    update_data = {}

    if metadata.get('title'):
        update_data['title'] = metadata['title']
    if metadata.get('description'):
        update_data['description'] = metadata['description']
    if metadata.get('favicon'):
        update_data['favicon'] = metadata['favicon']
    if metadata.get('og_image'):
        update_data['og_image'] = metadata['og_image']
    if metadata.get('twitter'):
        update_data['twitter'] = metadata['twitter']
    if metadata.get('discord'):
        update_data['discord'] = metadata['discord']
    if metadata.get('github'):
        update_data['github'] = metadata['github']

    if update_data:
        try:
            client.table('origins').update(update_data).eq('domain', domain).execute()
        except Exception as e:
            print(f"    Failed to update origin {domain}: {e}")

# ============================================
# SYNC HISTORY
# ============================================

def record_sync_history(client: 'Client', started_at: datetime, stats: dict, source_url: str = None):
    """Record sync run in history table"""
    try:
        client.table('sync_history').insert({
            'started_at': started_at.isoformat(),
            'completed_at': datetime.now(timezone.utc).isoformat(),
            'new_origins': stats.get('new_origins', 0),
            'updated_origins': stats.get('updated_origins', 0),
            'new_resources': stats.get('new_resources', 0),
            'updated_resources': stats.get('updated_resources', 0),
            'new_accepts': stats.get('new_accepts', 0),
            'errors': stats.get('errors', 0),
            'source_url': source_url,
        }).execute()
    except Exception as e:
        print(f"Failed to record sync history: {e}")

# ============================================
# LOCAL SAVE (FALLBACK)
# ============================================

def save_local(data: dict, output_dir: str = "data") -> tuple:
    """Save to local file as fallback"""
    os.makedirs(output_dir, exist_ok=True)

    timestamp = datetime.now(timezone.utc)
    filename = f"discovery_{timestamp.strftime('%Y-%m-%d_%H')}.json"
    filepath = os.path.join(output_dir, filename)

    with open(filepath, 'w') as f:
        json.dump(data, f)

    print(f"Saved to {filepath}")
    return filepath, filename


def upload_to_gcs(filepath: str, filename: str, bucket_name: str = "blockrun-data") -> bool:
    """Upload to Google Cloud Storage (legacy, for backup)"""
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
        return False
    except Exception as e:
        print(f"GCS upload failed: {e}")
        return False

# ============================================
# TRACTION SYNC (On-Chain USDC Transfers)
# ============================================

def get_expected_prices(client: 'Client', origin_id: str) -> List[float]:
    """Get all expected x402 payment prices for this origin."""
    try:
        result = client.table("accepts").select(
            "price_usd, resources!inner(origin_id)"
        ).eq("resources.origin_id", origin_id).execute()

        prices = []
        for a in result.data or []:
            if a.get("price_usd"):
                try:
                    prices.append(float(a["price_usd"]))
                except (ValueError, TypeError):
                    pass
        return prices
    except Exception as e:
        print(f"    Error fetching expected prices: {e}")
        return []


def is_valid_x402_transfer(amount: float, expected_prices: List[float]) -> bool:
    """
    Check if transfer amount matches x402 payment criteria.
    Price filter: ±10% of expected price
    """
    for price in expected_prices:
        if price <= 0:
            continue
        # Allow ±10% tolerance
        if price * 0.9 <= amount <= price * 1.1:
            return True
    return False


def get_base_traction(address: str, expected_prices: List[float]) -> Dict[str, Any]:
    """
    Get USDC transfers TO an address on Base using Alchemy.
    Only count transfers matching expected x402 prices (±10%).
    """
    if not ALCHEMY_API_KEY:
        return {"tx_count": 0, "volume": 0.0, "buyers": set(), "last_tx": None}

    try:
        url = f"https://base-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}"
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "alchemy_getAssetTransfers",
            "params": [{
                "toAddress": address,
                "contractAddresses": [USDC_ADDRESSES['base']],
                "category": ["erc20"],
                "withMetadata": True
            }]
        }

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method='POST'
        )

        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode())

        if "error" in data:
            print(f"    Alchemy error for {address[:10]}...: {data['error']}")
            return {"tx_count": 0, "volume": 0.0, "buyers": set(), "last_tx": None}

        transfers = data.get("result", {}).get("transfers", [])
        if not transfers:
            return {"tx_count": 0, "volume": 0.0, "buyers": set(), "last_tx": None}

        tx_count = 0
        volume = 0.0
        buyers: Set[str] = set()
        last_tx = None

        for t in transfers:
            amount = float(t.get("value", 0))

            # Only count if amount matches expected x402 price (±10%)
            if is_valid_x402_transfer(amount, expected_prices):
                tx_count += 1
                volume += amount
                if t.get("from"):
                    buyers.add(t["from"])
                ts = t.get("metadata", {}).get("blockTimestamp")
                if ts and (not last_tx or ts > last_tx):
                    last_tx = ts

        return {"tx_count": tx_count, "volume": volume, "buyers": buyers, "last_tx": last_tx}

    except Exception as e:
        print(f"    Error fetching Base traction for {address[:10]}...: {e}")
        return {"tx_count": 0, "volume": 0.0, "buyers": set(), "last_tx": None}


def get_solana_traction(address: str, expected_prices: List[float]) -> Dict[str, Any]:
    """
    Get USDC transfers TO an address on Solana using Helius.
    Only count transfers matching expected x402 prices (±10%).
    """
    if not HELIUS_API_KEY:
        return {"tx_count": 0, "volume": 0.0, "buyers": set(), "last_tx": None}

    try:
        url = f"https://api-mainnet.helius-rpc.com/v0/addresses/{address}/transactions/?api-key={HELIUS_API_KEY}"

        req = urllib.request.Request(url, headers={'Accept': 'application/json'})
        with urllib.request.urlopen(req, timeout=30) as response:
            txs = json.loads(response.read().decode())

        if not isinstance(txs, list):
            return {"tx_count": 0, "volume": 0.0, "buyers": set(), "last_tx": None}

        tx_count = 0
        volume = 0.0
        buyers: Set[str] = set()
        last_tx: Optional[int] = None

        for tx in txs:
            for transfer in tx.get("tokenTransfers", []):
                mint = transfer.get("mint", "")
                # Only count USDC transfers TO this address
                if mint == USDC_ADDRESSES['solana'] and transfer.get("toUserAccount") == address:
                    raw_amount = float(transfer.get("tokenAmount", 0))

                    # Only count if amount matches expected x402 price (±10%)
                    if is_valid_x402_transfer(raw_amount, expected_prices):
                        tx_count += 1
                        volume += raw_amount
                        from_user = transfer.get("fromUserAccount")
                        if from_user:
                            buyers.add(from_user)
                        ts = tx.get("timestamp")
                        if ts and (last_tx is None or ts > last_tx):
                            last_tx = ts

        # Convert Unix timestamp to ISO format
        last_tx_str = None
        if last_tx:
            last_tx_str = datetime.fromtimestamp(last_tx, tz=timezone.utc).isoformat()

        return {"tx_count": tx_count, "volume": volume, "buyers": buyers, "last_tx": last_tx_str}

    except Exception as e:
        print(f"    Error fetching Solana traction for {address[:10]}...: {e}")
        return {"tx_count": 0, "volume": 0.0, "buyers": set(), "last_tx": None}


def sync_traction_for_all_origins(client: 'Client'):
    """
    Sync on-chain traction data for all origins.
    Only counts USDC transfers matching expected x402 prices (±10%).
    """
    print("\n[Traction] Syncing on-chain USDC transfer data...")
    print(f"  Alchemy API: {'configured' if ALCHEMY_API_KEY else 'NOT SET'}")
    print(f"  Helius API: {'configured' if HELIUS_API_KEY else 'NOT SET'}")

    if not ALCHEMY_API_KEY and not HELIUS_API_KEY:
        print("  Skipping traction sync - no API keys configured")
        return

    # Get all origins
    try:
        result = client.table("origins").select("id, domain").execute()
        origins = result.data or []
    except Exception as e:
        print(f"  Error fetching origins: {e}")
        return

    print(f"  Processing {len(origins)} origins...")

    updated_count = 0
    skipped_count = 0

    for origin in origins:
        origin_id = origin["id"]
        domain = origin["domain"]

        # Get expected prices for this origin
        expected_prices = get_expected_prices(client, origin_id)
        if not expected_prices:
            skipped_count += 1
            continue

        total_tx = 0
        total_vol = 0.0
        all_buyers: Set[str] = set()
        last_tx: Optional[str] = None

        # Get Base traction
        if ALCHEMY_API_KEY:
            try:
                base_accepts = client.table("accepts").select(
                    "pay_to, resources!inner(origin_id)"
                ).eq("resources.origin_id", origin_id).eq("network", "base").execute()

                addresses = list(set(a["pay_to"] for a in (base_accepts.data or []) if a.get("pay_to")))
                for addr in addresses:
                    t = get_base_traction(addr, expected_prices)
                    total_tx += t["tx_count"]
                    total_vol += t["volume"]
                    all_buyers.update(t["buyers"])
                    if t["last_tx"] and (not last_tx or t["last_tx"] > last_tx):
                        last_tx = t["last_tx"]
            except Exception as e:
                print(f"    Error processing Base accepts for {domain}: {e}")

        # Get Solana traction
        if HELIUS_API_KEY:
            try:
                sol_accepts = client.table("accepts").select(
                    "pay_to, resources!inner(origin_id)"
                ).eq("resources.origin_id", origin_id).eq("network", "solana").execute()

                addresses = list(set(a["pay_to"] for a in (sol_accepts.data or []) if a.get("pay_to")))
                for addr in addresses:
                    t = get_solana_traction(addr, expected_prices)
                    total_tx += t["tx_count"]
                    total_vol += t["volume"]
                    all_buyers.update(t["buyers"])
                    if t["last_tx"] and (not last_tx or t["last_tx"] > last_tx):
                        last_tx = t["last_tx"]
            except Exception as e:
                print(f"    Error processing Solana accepts for {domain}: {e}")

        # Update origin if there's any traction
        if total_tx > 0:
            try:
                update_data = {
                    "total_transactions": total_tx,
                    "total_volume_usd": float(total_vol),
                    "unique_buyers": len(all_buyers),
                    "last_transaction_at": last_tx,
                    "traction_updated_at": datetime.now(timezone.utc).isoformat()
                }
                client.table("origins").update(update_data).eq("id", origin_id).execute()
                print(f"    {domain}: {total_tx} tx, ${total_vol:.2f} vol, {len(all_buyers)} buyers")
                updated_count += 1
            except Exception as e:
                print(f"    Error updating {domain}: {e}")
        else:
            skipped_count += 1

    print(f"\n  Traction sync complete: {updated_count} updated, {skipped_count} skipped")


# ============================================
# MAIN
# ============================================

def main():
    started_at = datetime.now(timezone.utc)

    print("=" * 60)
    print(f"x402 Discovery Pipeline - {started_at.isoformat()}")
    print("=" * 60)

    # 1. Fetch from all facilitators
    print("\n[1/5] Fetching from facilitators...")
    all_items = fetch_all_discovery()
    print(f"  Total fetched: {len(all_items)} items (testnets already filtered)")

    # 2. Deduplicate by resource URL
    print("\n[2/5] Deduplicating resources...")
    unique_items = deduplicate_resources(all_items)
    print(f"  Unique resources: {len(unique_items)} (removed {len(all_items) - len(unique_items)} duplicates)")

    # 3. Upsert to Supabase
    supabase = get_supabase_client()

    if supabase:
        print("\n[3/5] Upserting to Supabase...")
        new_origins, stats = upsert_to_supabase(supabase, unique_items)
        print(f"  New origins: {stats['new_origins']}")
        print(f"  Updated origins: {stats['updated_origins']}")
        print(f"  Resources: {stats['new_resources']}")
        print(f"  Accepts: {stats['new_accepts']}")
        print(f"  Errors: {stats['errors']}")

        # 4. Scrape metadata for new origins (use root domain)
        if new_origins:
            print(f"\n[4/5] Scraping metadata for {len(new_origins)} new origins...")
            scraped_roots = set()  # Track already scraped root domains
            for domain in new_origins:
                # Use root domain for scraping (e.g., api.lucyos.ai -> lucyos.ai)
                root_domain = get_root_domain(domain)
                if root_domain in scraped_roots:
                    # Copy metadata from already scraped root
                    continue
                scraped_roots.add(root_domain)

                print(f"    Scraping {root_domain} (for {domain})...")
                metadata = scrape_origin_metadata(root_domain)
                if any(metadata.values()):
                    update_origin_metadata(supabase, domain, metadata)
                time.sleep(0.5)  # Be nice to servers
        else:
            print("\n[4/5] No new origins to scrape")

        # 5. Record sync history
        print("\n[5/5] Recording sync history...")
        record_sync_history(supabase, started_at, stats)

        # 6. Sync on-chain traction data (if API keys configured)
        if ALCHEMY_API_KEY or HELIUS_API_KEY:
            print("\n[6/6] Syncing on-chain traction data...")
            sync_traction_for_all_origins(supabase)

    else:
        print("\n[3/5] Supabase not configured, saving locally...")
        # Fallback to local save
        data = {
            "timestamp": started_at.isoformat(),
            "items": unique_items,
            "count": len(unique_items),
        }
        filepath, filename = save_local(data)

        # Optional GCS upload
        bucket = os.environ.get("GCS_BUCKET", "blockrun-data")
        if os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCS_BUCKET"):
            upload_to_gcs(filepath, filename, bucket)

        stats = {'new_resources': len(unique_items)}

    # Summary
    print("\n" + "=" * 60)
    print(f"Pipeline complete!")
    print(f"  Duration: {(datetime.now(timezone.utc) - started_at).total_seconds():.1f}s")
    print(f"  Resources processed: {len(unique_items)}")
    print("=" * 60)


if __name__ == "__main__":
    main()
