#!/usr/bin/env python3
"""
Fetch all x402 service websites to get context for each service.
Output structured data.
"""

import json
import urllib.request
import urllib.error
from urllib.parse import urlparse
import os
import time
import re
import sys
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

# 跳过的托管平台域名
SKIP_PLATFORMS = [
    'vercel.app', 'railway.app', 'replit.dev', 'onrender.com',
    'ngrok-free.app', 'ngrok-free.dev', 'workers.dev', 'nx.link',
    'dctx.link', 'dev-mypinata.cloud', 'herokuapp.com', 'netlify.app',
    'pages.dev', 'fly.dev', 'run.app', 'cloudfunctions.net'
]


def fetch_page(url, timeout=15):
    """Fetch webpage content"""
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
        })
        with urllib.request.urlopen(req, timeout=timeout) as response:
            content_type = response.headers.get('Content-Type', '')
            if 'text/html' not in content_type and 'application/json' not in content_type:
                return None
            content = response.read().decode('utf-8', errors='ignore')
            return content[:100000]
    except Exception as e:
        return None


def extract_text_from_html(html):
    """Extract meaningful text from HTML"""
    if not html:
        return ""

    # Remove script and style
    html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<noscript[^>]*>.*?</noscript>', '', html, flags=re.DOTALL | re.IGNORECASE)

    # Extract title
    title_match = re.search(r'<title[^>]*>(.*?)</title>', html, re.IGNORECASE | re.DOTALL)
    title = title_match.group(1).strip() if title_match else ""

    # Extract meta description
    desc_match = re.search(r'<meta[^>]*name=["\']description["\'][^>]*content=["\'](.*?)["\']', html, re.IGNORECASE)
    if not desc_match:
        desc_match = re.search(r'<meta[^>]*content=["\'](.*?)["\'][^>]*name=["\']description["\']', html, re.IGNORECASE)
    meta_desc = desc_match.group(1).strip() if desc_match else ""

    # Extract og:description
    og_match = re.search(r'<meta[^>]*property=["\']og:description["\'][^>]*content=["\'](.*?)["\']', html, re.IGNORECASE)
    og_desc = og_match.group(1).strip() if og_match else ""

    # Remove HTML tags to get body text
    text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'\s+', ' ', text).strip()

    # Clean common meaningless content
    text = re.sub(r'(Loading|Please wait|JavaScript required)\.{0,3}', '', text, flags=re.IGNORECASE)

    return {
        'title': title[:200],
        'meta_description': meta_desc[:500],
        'og_description': og_desc[:500],
        'body_text': text[:3000]
    }


def load_discovery_data(filepath=None):
    """Load discovery data - uses latest file if no filepath specified"""
    if filepath is None:
        import glob
        files = sorted(glob.glob("data/discovery_*.json"))
        if not files:
            raise FileNotFoundError("No discovery files found in data/")
        filepath = files[-1]  # Latest file
        print(f"Using latest discovery file: {filepath}")
    with open(filepath) as f:
        return json.load(f)


def extract_services_by_domain(data):
    """Group services by domain"""
    services_by_domain = {}

    for facilitator, info in data.get('facilitators', {}).items():
        for item in info.get('items', []):
            resource = item.get('resource', '')
            if not resource:
                continue

            parsed = urlparse(resource)
            domain = parsed.netloc

            if not domain or domain.startswith('0x') or '...' in domain:
                continue

            # Get root domain
            parts = domain.split('.')
            if len(parts) >= 2:
                root_domain = '.'.join(parts[-2:])
            else:
                root_domain = domain

            # Skip hosting platforms
            if any(root_domain.endswith(p) for p in SKIP_PLATFORMS):
                continue

            if root_domain not in services_by_domain:
                services_by_domain[root_domain] = {
                    'full_domains': set(),
                    'services': []
                }

            services_by_domain[root_domain]['full_domains'].add(domain)

            # Extract service info
            service_info = {
                'resource': resource,
                'path': parsed.path,
                'facilitator': facilitator,
            }

            if item.get('accepts'):
                accept = item['accepts'][0]
                service_info['description'] = accept.get('description', '')
                service_info['price'] = accept.get('maxAmountRequired', '')
                service_info['network'] = accept.get('network', '')
                service_info['payTo'] = accept.get('payTo', '')

                output_schema = accept.get('outputSchema', {})
                if isinstance(output_schema, dict):
                    input_schema = output_schema.get('input', {})
                    if isinstance(input_schema, dict):
                        service_info['method'] = input_schema.get('method', '')
                        if input_schema.get('bodyFields'):
                            service_info['input_fields'] = list(input_schema['bodyFields'].keys())

            services_by_domain[root_domain]['services'].append(service_info)

    # Convert set to list
    for domain in services_by_domain:
        services_by_domain[domain]['full_domains'] = list(services_by_domain[domain]['full_domains'])

    return services_by_domain


def analyze_domain(domain, services_data):
    """Analyze a single domain"""
    result = {
        'domain': domain,
        'service_count': len(services_data['services']),
        'subdomains': services_data['full_domains'],
        'website': None,
        'services': [],
        'category': None,
        'fetched_at': datetime.now(timezone.utc).isoformat()
    }

    # Try to fetch website
    page_content = None
    tried_urls = []

    for url in [f"https://{domain}", f"https://www.{domain}", f"http://{domain}"]:
        tried_urls.append(url)
        page_content = fetch_page(url)
        if page_content:
            result['website'] = {
                'url': url,
                **extract_text_from_html(page_content)
            }
            break
        time.sleep(0.3)

    if not result['website']:
        # Try with first full subdomain
        if services_data['full_domains']:
            first_subdomain = services_data['full_domains'][0]
            url = f"https://{first_subdomain}"
            page_content = fetch_page(url)
            if page_content:
                result['website'] = {
                    'url': url,
                    **extract_text_from_html(page_content)
                }

    # Collect service info
    descriptions = set()
    networks = set()
    methods = set()
    endpoints = []

    for svc in services_data['services']:
        if svc.get('description'):
            descriptions.add(svc['description'][:200])
        if svc.get('network'):
            networks.add(svc['network'])
        if svc.get('method'):
            methods.add(svc['method'])
        endpoints.append(svc.get('path', ''))

    # Get sample services
    sample_services = []
    for svc in services_data['services'][:10]:
        sample_services.append({
            'endpoint': svc.get('path', ''),
            'description': svc.get('description', '')[:200],
            'price': svc.get('price', ''),
            'network': svc.get('network', ''),
            'method': svc.get('method', ''),
            'input_fields': svc.get('input_fields', [])
        })

    result['services'] = sample_services
    result['all_descriptions'] = list(descriptions)[:20]
    result['networks'] = list(networks)
    result['methods'] = list(methods)
    result['unique_endpoints'] = list(set(endpoints))[:30]

    # Infer category
    result['category'] = infer_category(result)

    return result


def infer_category(data):
    """Infer service category based on content"""
    text = ""
    if data.get('website'):
        text += data['website'].get('title', '') + " "
        text += data['website'].get('meta_description', '') + " "
        text += data['website'].get('body_text', '')[:500] + " "

    for desc in data.get('all_descriptions', [])[:5]:
        text += desc + " "

    text = text.lower()

    # Category rules
    categories = {
        'ai_agent': ['agent', 'swarm', 'autonomous', 'ai agent', 'workflow', 'automation'],
        'llm_inference': ['llm', 'gpt', 'inference', 'language model', 'chat completion', 'text generation'],
        'blockchain_data': ['blockchain', 'on-chain', 'transaction', 'wallet', 'token', 'defi', 'dex'],
        'social_media': ['twitter', 'x.com', 'tiktok', 'youtube', 'social', 'sentiment'],
        'trading': ['trading', 'swap', 'exchange', 'price', 'market', 'futures', 'funding rate'],
        'security': ['security', 'risk', 'compliance', 'audit', 'phishing', 'scam'],
        'developer_tools': ['api', 'sdk', 'developer', 'tool', 'utility', 'rpc'],
        'nft': ['nft', 'mint', 'collection', 'opensea'],
        'payment': ['payment', 'pay', 'usdc', 'facilitator', 'micropayment'],
        'content': ['content', 'image', 'video', 'media', 'generate'],
    }

    for category, keywords in categories.items():
        if any(kw in text for kw in keywords):
            return category

    return 'other'


def main():
    print("=" * 70)
    print(f"Fetching ALL x402 service context - {datetime.now(timezone.utc).isoformat()}")
    print("=" * 70)

    # Load data
    data = load_discovery_data()
    services_by_domain = extract_services_by_domain(data)

    # Sort by service count
    sorted_domains = sorted(
        services_by_domain.items(),
        key=lambda x: -len(x[1]['services'])
    )

    print(f"Total domains to process: {len(sorted_domains)}")
    print()

    results = {}
    failed = []

    for i, (domain, services_data) in enumerate(sorted_domains):
        service_count = len(services_data['services'])
        print(f"[{i+1}/{len(sorted_domains)}] {domain} ({service_count} services)...", end=" ", flush=True)

        try:
            result = analyze_domain(domain, services_data)
            results[domain] = result

            if result.get('website'):
                print(f"✓ {result['category']}")
            else:
                print(f"- no website (category: {result['category']})")
        except Exception as e:
            print(f"x error: {e}")
            failed.append(domain)

        # Rate limiting
        if i % 10 == 0:
            time.sleep(1)

    # Save results
    output = {
        'fetched_at': datetime.now(timezone.utc).isoformat(),
        'total_domains': len(results),
        'failed_domains': failed,
        'domains': results
    }

    output_file = "all_services_context.json"
    with open(output_file, 'w') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print()
    print("=" * 70)
    print(f"✓ Saved to {output_file}")
    print(f"  Processed: {len(results)} domains")
    print(f"  Failed: {len(failed)} domains")

    # Category statistics
    categories = {}
    for domain, data in results.items():
        cat = data.get('category', 'other')
        categories[cat] = categories.get(cat, 0) + 1

    print()
    print("Categories:")
    for cat, count in sorted(categories.items(), key=lambda x: -x[1]):
        print(f"  {cat}: {count}")


if __name__ == "__main__":
    main()
