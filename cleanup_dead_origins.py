#!/usr/bin/env python3
"""
Cleanup Dead Origins
Removes origins whose root domain is not accessible (DNS error, connection refused, etc.)

Usage:
    python cleanup_dead_origins.py
"""

import os
import urllib.request
import urllib.error
import ssl
import socket
from typing import Tuple

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


def get_root_domain(domain: str) -> str:
    """Extract root domain for checking."""
    if not domain:
        return domain

    parts = domain.split('.')
    special_tlds = ['co.uk', 'com.au', 'co.nz', 'co.jp', 'com.br']
    domain_lower = domain.lower()

    for tld in special_tlds:
        if domain_lower.endswith('.' + tld):
            prefix = domain_lower[:-len(tld)-1]
            if '.' in prefix:
                return prefix.split('.')[-1] + '.' + tld
            return domain

    if len(parts) >= 2:
        return '.'.join(parts[-2:])

    return domain


def check_domain_alive(domain: str, timeout: int = 10) -> Tuple[bool, str]:
    """
    Check if a domain is accessible.
    Returns (is_alive, error_message)
    """
    try:
        url = f"https://{domain}"
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            'Accept': 'text/html',
        })

        # Create SSL context that's more lenient (some sites have cert issues but are still "alive")
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as response:
            # If we get any response, the domain is alive
            return True, "OK"

    except urllib.error.HTTPError as e:
        # HTTP errors mean the server is responding (just returning an error)
        # 4xx and 5xx errors still mean the site exists
        return True, f"HTTP {e.code}"

    except urllib.error.URLError as e:
        reason = str(e.reason)
        # These are fatal errors - domain doesn't exist or can't connect
        if 'nodename nor servname provided' in reason:
            return False, "DNS_ERROR"
        elif 'Name or service not known' in reason:
            return False, "DNS_ERROR"
        elif 'Connection refused' in reason:
            return False, "CONNECTION_REFUSED"
        elif 'No route to host' in reason:
            return False, "NO_ROUTE"
        elif 'Network is unreachable' in reason:
            return False, "NETWORK_UNREACHABLE"
        else:
            # Other URL errors might be temporary
            return True, f"URL_ERROR: {reason[:50]}"

    except socket.timeout:
        # Timeout might be temporary - keep it
        return True, "TIMEOUT"

    except Exception as e:
        # Unknown errors - be conservative and keep
        return True, f"ERROR: {str(e)[:50]}"


def main():
    print("=" * 60)
    print("Cleanup Dead Origins")
    print("=" * 60)

    # Connect to Supabase
    url = os.environ.get('SUPABASE_URL')
    key = os.environ.get('SUPABASE_SERVICE_KEY')

    if not url or not key:
        print("Error: SUPABASE_URL or SUPABASE_SERVICE_KEY not set")
        return

    supabase = create_client(url, key)

    # Get all origins with pagination
    print("\nFetching origins from database...")
    origins = []
    offset = 0
    limit = 1000
    while True:
        result = supabase.table('origins').select('id, domain').range(offset, offset + limit - 1).execute()
        origins.extend(result.data)
        if len(result.data) < limit:
            break
        offset += limit

    print(f"Found {len(origins)} origins")

    # Track checked root domains
    checked_roots = {}  # root_domain -> (is_alive, error)
    dead_origins = []
    alive_count = 0

    for i, origin in enumerate(origins):
        domain = origin['domain']
        root_domain = get_root_domain(domain)

        # Check if we already tested this root
        if root_domain in checked_roots:
            is_alive, error = checked_roots[root_domain]
            status = "cached"
        else:
            is_alive, error = check_domain_alive(root_domain)
            checked_roots[root_domain] = (is_alive, error)
            status = "checked"

        if is_alive:
            alive_count += 1
            if (i + 1) % 50 == 0:
                print(f"  [{i+1}/{len(origins)}] Checked... ({alive_count} alive, {len(dead_origins)} dead)")
        else:
            dead_origins.append({
                'id': origin['id'],
                'domain': domain,
                'root_domain': root_domain,
                'error': error
            })
            print(f"  [{i+1}/{len(origins)}] DEAD: {domain} ({root_domain}) - {error}")

    print(f"\n{'=' * 60}")
    print(f"Results:")
    print(f"  Total origins: {len(origins)}")
    print(f"  Alive: {alive_count}")
    print(f"  Dead: {len(dead_origins)}")
    print(f"  Unique root domains checked: {len(checked_roots)}")

    if dead_origins:
        print(f"\nDead origins to remove:")
        for d in dead_origins[:20]:  # Show first 20
            print(f"  - {d['domain']} ({d['error']})")
        if len(dead_origins) > 20:
            print(f"  ... and {len(dead_origins) - 20} more")

        # Ask for confirmation before deleting
        print(f"\nRemoving {len(dead_origins)} dead origins...")

        for d in dead_origins:
            try:
                # Delete origin (cascades to resources and accepts)
                supabase.table('origins').delete().eq('id', d['id']).execute()
            except Exception as e:
                print(f"  Error deleting {d['domain']}: {e}")

        print(f"Deleted {len(dead_origins)} dead origins")

    print("=" * 60)
    print("Cleanup Complete!")


if __name__ == "__main__":
    main()
