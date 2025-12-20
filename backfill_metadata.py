#!/usr/bin/env python3
"""
Backfill Origin Metadata
Scrapes metadata for all origins using their root domain.
e.g., api-dev.agents.skillfulai.io -> scrape skillfulai.io

Usage:
    python backfill_metadata.py
"""

import os
import re
import time
import urllib.request
from typing import Optional

try:
    from supabase import create_client, Client
except ImportError:
    print("Error: supabase not installed. Run: pip install supabase")
    exit(1)

try:
    from bs4 import BeautifulSoup
except ImportError:
    print("Error: beautifulsoup4 not installed. Run: pip install beautifulsoup4 lxml")
    exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def get_root_domain(domain: str) -> str:
    """
    Extract root domain for scraping.
    e.g., api-dev.agents.skillfulai.io -> skillfulai.io
          data-x402.hexens.io -> hexens.io
          x402.lucyos.ai -> lucyos.ai
    """
    if not domain:
        return domain

    parts = domain.split('.')

    # Handle special TLDs like .co.uk, .com.au
    special_tlds = ['co.uk', 'com.au', 'co.nz', 'co.jp', 'com.br']
    domain_lower = domain.lower()

    for tld in special_tlds:
        if domain_lower.endswith('.' + tld):
            prefix = domain_lower[:-len(tld)-1]
            if '.' in prefix:
                return prefix.split('.')[-1] + '.' + tld
            return domain

    # For normal domains, return last 2 parts
    if len(parts) >= 2:
        return '.'.join(parts[-2:])

    return domain


def scrape_origin_metadata(domain: str) -> dict:
    """
    Scrape metadata from origin domain.
    Returns dict with: title, description, favicon, og_image, twitter, discord, github
    """
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

        with urllib.request.urlopen(req, timeout=15) as response:
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
                match = re.search(r'(?:twitter\.com|x\.com)/([^/?]+)', href)
                if match and match.group(1) not in ['share', 'intent', 'home']:
                    metadata['twitter'] = match.group(1)
            elif 'discord.gg/' in href or 'discord.com/' in href:
                metadata['discord'] = a['href']
            elif 'github.com/' in href:
                match = re.search(r'github\.com/([^/?]+)', href)
                if match:
                    metadata['github'] = match.group(1)

    except Exception as e:
        print(f"    Failed to scrape {domain}: {e}")

    return metadata


def main():
    print("=" * 60)
    print("Backfill Origin Metadata")
    print("=" * 60)

    # Connect to Supabase
    url = os.environ.get('SUPABASE_URL')
    key = os.environ.get('SUPABASE_SERVICE_KEY')

    if not url or not key:
        print("Error: SUPABASE_URL or SUPABASE_SERVICE_KEY not set")
        return

    supabase = create_client(url, key)

    # Get all origins that need metadata (with pagination)
    print("\nFetching origins from database...")
    origins = []
    offset = 0
    limit = 1000
    while True:
        result = supabase.table('origins').select('id, domain, title, description').range(offset, offset + limit - 1).execute()
        origins.extend(result.data)
        if len(result.data) < limit:
            break
        offset += limit

    print(f"Found {len(origins)} origins")

    # Track scraped root domains to avoid duplicates
    scraped_roots = {}  # root_domain -> metadata
    updated = 0
    skipped = 0
    failed = 0

    for i, origin in enumerate(origins):
        domain = origin['domain']
        has_title = origin.get('title')
        has_desc = origin.get('description')

        # Skip if already has metadata
        if has_title and has_desc:
            skipped += 1
            continue

        # Get root domain
        root_domain = get_root_domain(domain)

        print(f"\n[{i+1}/{len(origins)}] {domain} -> {root_domain}")

        # Check if we already scraped this root
        if root_domain in scraped_roots:
            metadata = scraped_roots[root_domain]
            print(f"    Using cached metadata from {root_domain}")
        else:
            # Scrape the root domain
            print(f"    Scraping {root_domain}...")
            metadata = scrape_origin_metadata(root_domain)
            scraped_roots[root_domain] = metadata
            time.sleep(0.5)  # Be nice to servers

        # Update origin if we got any metadata
        if any(metadata.values()):
            update_data = {}
            if metadata.get('title') and not has_title:
                update_data['title'] = metadata['title']
            if metadata.get('description') and not has_desc:
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
                    supabase.table('origins').update(update_data).eq('id', origin['id']).execute()
                    print(f"    Updated: title={update_data.get('title', 'N/A')[:30]}...")
                    updated += 1
                except Exception as e:
                    print(f"    Failed to update: {e}")
                    failed += 1
        else:
            print(f"    No metadata found")
            failed += 1

    print("\n" + "=" * 60)
    print(f"Backfill Complete!")
    print(f"  Updated: {updated}")
    print(f"  Skipped (already had data): {skipped}")
    print(f"  Failed: {failed}")
    print(f"  Unique root domains scraped: {len(scraped_roots)}")
    print("=" * 60)


if __name__ == "__main__":
    main()
