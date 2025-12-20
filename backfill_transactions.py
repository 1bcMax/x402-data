#!/usr/bin/env python3
"""
Backfill Transaction Data from Blockchain
Fetches USDC transfers to pay_to addresses from accepts table.

Uses:
- Base: Basescan API
- Solana: Helius or public RPC

Usage:
    # Set API keys in .env:
    # BASESCAN_API_KEY=your-key
    # HELIUS_API_KEY=your-key (optional, for Solana)

    python backfill_transactions.py
"""

import os
import json
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Optional, List, Dict

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

# USDC contract addresses
USDC_CONTRACTS = {
    'base': '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913',
    'polygon': '0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359',
    'arbitrum': '0xaf88d065e77c8cC2239327C5EDb3A432268e5831',
}

# USDC has 6 decimals
USDC_DECIMALS = 6

# API endpoints
BASESCAN_API = "https://api.basescan.org/api"
POLYGONSCAN_API = "https://api.polygonscan.com/api"


def fetch_erc20_transfers(
    address: str,
    contract: str,
    api_url: str,
    api_key: str,
    start_block: int = 0
) -> List[Dict]:
    """Fetch ERC20 token transfers to an address"""
    transfers = []

    params = {
        'module': 'account',
        'action': 'tokentx',
        'contractaddress': contract,
        'address': address,
        'startblock': start_block,
        'endblock': 99999999,
        'sort': 'desc',
        'apikey': api_key
    }

    url = f"{api_url}?" + "&".join(f"{k}={v}" for k, v in params.items())

    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'BlockRun/1.0',
            'Accept': 'application/json'
        })

        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode())

            if data.get('status') == '1' and data.get('result'):
                for tx in data['result']:
                    # Only count incoming transfers (to this address)
                    if tx.get('to', '').lower() == address.lower():
                        transfers.append({
                            'tx_hash': tx.get('hash'),
                            'from_address': tx.get('from'),
                            'to_address': tx.get('to'),
                            'value': int(tx.get('value', 0)) / (10 ** USDC_DECIMALS),
                            'timestamp': datetime.fromtimestamp(
                                int(tx.get('timeStamp', 0)),
                                tz=timezone.utc
                            ).isoformat(),
                            'block_number': int(tx.get('blockNumber', 0)),
                        })

    except Exception as e:
        print(f"    Error fetching transfers for {address[:10]}...: {e}")

    return transfers


def fetch_solana_transfers(
    address: str,
    helius_key: Optional[str] = None
) -> List[Dict]:
    """Fetch USDC transfers to a Solana address using Helius API"""
    transfers = []

    if not helius_key:
        print(f"    Skipping Solana address {address[:10]}... (no HELIUS_API_KEY)")
        return transfers

    # Helius enhanced transactions API
    url = f"https://api.helius.xyz/v0/addresses/{address}/transactions"
    params = f"?api-key={helius_key}&type=TRANSFER"

    try:
        req = urllib.request.Request(url + params, headers={
            'User-Agent': 'BlockRun/1.0',
            'Accept': 'application/json'
        })

        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode())

            for tx in data:
                # Filter for USDC transfers
                if tx.get('type') == 'TRANSFER':
                    for transfer in tx.get('tokenTransfers', []):
                        if 'USDC' in transfer.get('mint', '').upper() or \
                           transfer.get('mint') == 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v':
                            if transfer.get('toUserAccount') == address:
                                transfers.append({
                                    'tx_hash': tx.get('signature'),
                                    'from_address': transfer.get('fromUserAccount'),
                                    'to_address': transfer.get('toUserAccount'),
                                    'value': transfer.get('tokenAmount', 0),
                                    'timestamp': datetime.fromtimestamp(
                                        tx.get('timestamp', 0),
                                        tz=timezone.utc
                                    ).isoformat(),
                                })

    except Exception as e:
        print(f"    Error fetching Solana transfers for {address[:10]}...: {e}")

    return transfers


def main():
    print("=" * 60)
    print("Backfill Transaction Data")
    print("=" * 60)

    # Get API keys
    basescan_key = os.environ.get('BASESCAN_API_KEY')
    helius_key = os.environ.get('HELIUS_API_KEY')

    if not basescan_key:
        print("\nWarning: BASESCAN_API_KEY not set. Using free tier (rate limited).")
        basescan_key = 'YourApiKeyToken'  # Basescan allows limited free calls

    # Connect to Supabase
    url = os.environ.get('SUPABASE_URL')
    key = os.environ.get('SUPABASE_SERVICE_KEY')

    if not url or not key:
        print("Error: SUPABASE_URL or SUPABASE_SERVICE_KEY not set")
        return

    supabase = create_client(url, key)

    # Get unique pay_to addresses grouped by network
    print("\nFetching pay_to addresses from accepts table...")
    result = supabase.table('accepts').select('pay_to, network').execute()

    # Group by network
    addresses_by_network = {}
    for accept in result.data:
        network = accept.get('network', '')
        pay_to = accept.get('pay_to', '')
        if pay_to and network:
            if network not in addresses_by_network:
                addresses_by_network[network] = set()
            addresses_by_network[network].add(pay_to)

    print(f"Found addresses across networks:")
    for network, addrs in addresses_by_network.items():
        print(f"  {network}: {len(addrs)} unique addresses")

    total_transactions = 0

    # Process Base addresses
    if 'base' in addresses_by_network:
        print(f"\n--- Processing Base ({len(addresses_by_network['base'])} addresses) ---")
        for i, address in enumerate(addresses_by_network['base']):
            print(f"[{i+1}/{len(addresses_by_network['base'])}] {address[:10]}...")

            transfers = fetch_erc20_transfers(
                address=address,
                contract=USDC_CONTRACTS['base'],
                api_url=BASESCAN_API,
                api_key=basescan_key
            )

            if transfers:
                print(f"    Found {len(transfers)} transfers")

                # Insert transactions
                for tx in transfers:
                    try:
                        tx_data = {
                            'wallet_address': tx['from_address'],
                            'model': 'unknown',  # We don't know which API was called
                            'price_charged': tx['value'],
                            'network': 'base',
                            'tx_hash': tx['tx_hash'],
                            'status': 'completed',
                            'created_at': tx['timestamp'],
                        }
                        supabase.table('transactions').insert(tx_data).execute()
                        total_transactions += 1
                    except Exception as e:
                        # Ignore duplicates
                        if 'duplicate' not in str(e).lower():
                            print(f"    Insert error: {e}")

            time.sleep(0.25)  # Rate limit

    # Process Solana addresses
    if 'solana' in addresses_by_network and helius_key:
        print(f"\n--- Processing Solana ({len(addresses_by_network['solana'])} addresses) ---")
        for i, address in enumerate(addresses_by_network['solana']):
            print(f"[{i+1}/{len(addresses_by_network['solana'])}] {address[:10]}...")

            transfers = fetch_solana_transfers(address, helius_key)

            if transfers:
                print(f"    Found {len(transfers)} transfers")

                for tx in transfers:
                    try:
                        tx_data = {
                            'wallet_address': tx['from_address'],
                            'model': 'unknown',
                            'price_charged': tx['value'],
                            'network': 'solana',
                            'tx_hash': tx['tx_hash'],
                            'status': 'completed',
                            'created_at': tx['timestamp'],
                        }
                        supabase.table('transactions').insert(tx_data).execute()
                        total_transactions += 1
                    except Exception as e:
                        if 'duplicate' not in str(e).lower():
                            print(f"    Insert error: {e}")

            time.sleep(0.5)

    print("\n" + "=" * 60)
    print(f"Backfill Complete!")
    print(f"  Total transactions imported: {total_transactions}")
    print("=" * 60)


if __name__ == "__main__":
    main()
