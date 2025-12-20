#!/usr/bin/env python3
"""
抓取 x402 服务网站，获取每个 agent/服务的 context
用 LLM 总结：这个服务是干嘛的
"""

import json
import urllib.request
import urllib.error
from urllib.parse import urlparse
import os
import time
from datetime import datetime, timezone

# 需要抓取的主要域名
PRIORITY_DOMAINS = [
    "questflow.ai",
    "heurist.xyz",
    "x420.dev",
    "aurracloud.com",
    "portalsprotocol.com",
    "grapevine.fyi",
    "slamai.dev",
    "x402labs.dev",
    "crestal.network",
    "dirtroad.dev",
    "codenut.ai",
    "jatevo.ai",
    "payai.network",
    "blocksec.ai",
    "lucyos.ai",
    "aiape.tech",
    "t54.ai",
]


def fetch_page(url, timeout=10):
    """抓取网页内容"""
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (compatible; BlockRun/1.0)'
        })
        with urllib.request.urlopen(req, timeout=timeout) as response:
            content = response.read().decode('utf-8', errors='ignore')
            return content[:50000]  # 限制大小
    except Exception as e:
        return None


def extract_text_from_html(html):
    """简单提取 HTML 中的文本"""
    import re
    # 移除 script 和 style
    html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
    # 移除 HTML 标签
    text = re.sub(r'<[^>]+>', ' ', html)
    # 清理空白
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:5000]  # 限制长度


def load_discovery_data(filepath="data/discovery_2025-12-19_05.json"):
    """加载 discovery 数据"""
    with open(filepath) as f:
        return json.load(f)


def extract_services_by_domain(data):
    """按域名分组服务"""
    services_by_domain = {}

    for facilitator, info in data.get('facilitators', {}).items():
        for item in info.get('items', []):
            resource = item.get('resource', '')
            if not resource:
                continue

            parsed = urlparse(resource)
            domain = parsed.netloc

            # 获取根域名
            parts = domain.split('.')
            if len(parts) >= 2:
                root_domain = '.'.join(parts[-2:])
            else:
                root_domain = domain

            if root_domain not in services_by_domain:
                services_by_domain[root_domain] = []

            # 提取服务信息
            service_info = {
                'resource': resource,
                'facilitator': facilitator,
            }

            if item.get('accepts'):
                accept = item['accepts'][0]
                service_info['description'] = accept.get('description', '')
                service_info['price'] = accept.get('maxAmountRequired', '')
                service_info['network'] = accept.get('network', '')

                # 提取 input schema
                output_schema = accept.get('outputSchema', {})
                if isinstance(output_schema, dict):
                    input_schema = output_schema.get('input', {})
                    if isinstance(input_schema, dict) and input_schema.get('bodyFields'):
                        service_info['input_fields'] = list(
                            input_schema['bodyFields'].keys()
                        )

            services_by_domain[root_domain].append(service_info)

    return services_by_domain


def summarize_domain(domain, services, page_content=None):
    """总结一个域名下的所有服务"""
    summary = {
        'domain': domain,
        'service_count': len(services),
        'services': [],
        'website_text': None,
    }

    # 收集所有服务的描述
    descriptions = set()
    endpoints = []

    for svc in services[:20]:  # 最多取20个
        if svc.get('description'):
            descriptions.add(svc['description'])
        endpoints.append(urlparse(svc['resource']).path)

        summary['services'].append({
            'endpoint': urlparse(svc['resource']).path,
            'description': svc.get('description', ''),
            'price': svc.get('price', ''),
            'input_fields': svc.get('input_fields', []),
        })

    summary['unique_descriptions'] = list(descriptions)[:10]

    if page_content:
        summary['website_text'] = extract_text_from_html(page_content)[:2000]

    return summary


def main():
    print("=" * 60)
    print("Fetching x402 service context")
    print("=" * 60)

    # 加载数据
    data = load_discovery_data()
    services_by_domain = extract_services_by_domain(data)

    print(f"Total domains: {len(services_by_domain)}")

    # 按优先级处理
    results = {}

    for domain in PRIORITY_DOMAINS:
        if domain not in services_by_domain:
            print(f"Skipping {domain} (not in data)")
            continue

        services = services_by_domain[domain]
        print(f"\nProcessing {domain} ({len(services)} services)...")

        # 抓取网站首页
        page_content = None
        for url in [f"https://{domain}", f"https://www.{domain}"]:
            print(f"  Fetching {url}...")
            page_content = fetch_page(url)
            if page_content:
                print(f"  ✓ Got {len(page_content)} chars")
                break
            time.sleep(1)

        # 总结
        summary = summarize_domain(domain, services, page_content)
        results[domain] = summary

        time.sleep(0.5)

    # 保存结果
    output_file = "domain_context.json"
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)

    print(f"\n✓ Saved to {output_file}")
    print(f"Processed {len(results)} domains")


if __name__ == "__main__":
    main()
