# x402 Data Pipeline Architecture

This document provides a comprehensive overview of the x402 discovery data pipeline, designed for both human developers and AI agents to understand the data model, sources, and flow.

## Overview

The x402 data pipeline collects and aggregates service discovery data from the x402 payment protocol ecosystem. It runs hourly on Google Cloud Run and stores data in Supabase (PostgreSQL).

```
┌─────────────────────────────────────────────────────────────────┐
│                    HOURLY PIPELINE FLOW                          │
└─────────────────────────────────────────────────────────────────┘
                              │
    ┌─────────────────────────┼─────────────────────────────┐
    │                         │                             │
    ▼                         ▼                             ▼
┌─────────┐            ┌─────────┐                   ┌─────────┐
│   CDP   │            │  PayAI  │        ...        │Thirdweb │
│Coinbase │            │         │                   │         │
└────┬────┘            └────┬────┘                   └────┬────┘
     │                      │                             │
     └──────────────────────┼─────────────────────────────┘
                            │
                            ▼
              ┌─────────────────────────┐
              │  Deduplicate & Filter   │
              │  (testnet, hosting)     │
              └─────────────────────────┘
                            │
                            ▼
              ┌─────────────────────────┐
              │   Upsert to Supabase    │
              │ origins→resources→accepts│
              └─────────────────────────┘
                            │
          ┌─────────────────┼─────────────────┐
          │                 │                 │
          ▼                 ▼                 ▼
    ┌───────────┐    ┌───────────┐    ┌───────────┐
    │  Scrape   │    │   Auto    │    │  Sync     │
    │  Origins  │    │   Tag     │    │ On-chain  │
    │(metadata) │    │ Resources │    │ Traction  │
    └───────────┘    └───────────┘    └───────────┘
```

## Data Sources

### Facilitator Discovery Endpoints

| Facilitator | Endpoint | Response Format |
|-------------|----------|-----------------|
| CDP Coinbase | `https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources` | `{ items: [...] }` |
| PayAI | `https://facilitator.payai.network/discovery/resources` | `{ items: [...] }` |
| Questflow | `https://facilitator.questflow.ai/discovery/resources` | `{ items: [...] }` |
| AnySpend | `https://mainnet.anyspend.com/x402/discovery/resources` | `{ data: { items: [...] } }` |
| AurraCloud | `https://x402-facilitator.aurracloud.com/discovery/resources` | `{ items: [...] }` |
| Thirdweb | `https://api.thirdweb.com/v1/payments/x402/discovery/resources` | `{ items: [...] }` |

All endpoints support pagination via `?offset=N&limit=M` query parameters.

### On-chain Data Sources

| Chain | Provider | Data |
|-------|----------|------|
| Base | Alchemy API | USDC transfer events to `payTo` addresses |
| Solana | Helius API | USDC transfer events |

## Database Schema

### Origins Table

Represents a domain/project hosting x402 services.

```sql
CREATE TABLE origins (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  origin TEXT UNIQUE NOT NULL,        -- "https://api.example.com"
  domain TEXT NOT NULL,               -- "api.example.com"

  -- Scraped metadata (from homepage)
  title TEXT,
  description TEXT,
  favicon TEXT,
  og_image TEXT,
  twitter TEXT,
  discord TEXT,
  github TEXT,

  -- Aggregated stats
  resource_count INTEGER DEFAULT 0,

  -- Flags
  verified BOOLEAN DEFAULT FALSE,
  featured BOOLEAN DEFAULT FALSE,

  -- On-chain traction (synced from blockchain)
  total_transactions INTEGER,
  total_volume_usd DECIMAL(20,6),
  unique_buyers INTEGER,
  last_transaction_at TIMESTAMPTZ,
  traction_synced_at TIMESTAMPTZ,

  -- Timestamps
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

### Resources Table

Represents individual x402-enabled API endpoints.

```sql
CREATE TABLE resources (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  origin_id UUID REFERENCES origins(id) ON DELETE CASCADE,
  resource TEXT UNIQUE NOT NULL,      -- Full URL
  path TEXT,                          -- URL path only

  -- x402 protocol fields
  type TEXT DEFAULT 'http',
  x402_version INTEGER DEFAULT 1,
  method TEXT DEFAULT 'POST',
  description TEXT,
  last_updated TIMESTAMPTZ,

  -- Legacy metadata fields (JSON blobs)
  metadata JSONB,                     -- Raw item.metadata
  input_schema JSONB,                 -- item.inputSchema
  item_output_schema JSONB,           -- item.outputSchema

  -- x402 v2 Bazaar metadata fields (parsed)
  example_input JSONB,                -- metadata.input (example request)
  example_output JSONB,               -- metadata.output (example response)
  input_schema_v2 JSONB,              -- metadata.inputSchema (JSON Schema)
  output_schema_v2 JSONB,             -- metadata.outputSchema (JSON Schema)
  self_reported_category TEXT,        -- Bazaar extension category
  self_reported_tags TEXT[],          -- Bazaar extension tags array

  -- Timestamps
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

### Accepts Table

Represents payment options for each resource.

```sql
CREATE TABLE accepts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  resource_id UUID REFERENCES resources(id) ON DELETE CASCADE,

  -- Payment details
  scheme TEXT DEFAULT 'exact',        -- "exact", "subscription"
  network TEXT NOT NULL,              -- "base", "solana", "polygon"
  asset TEXT NOT NULL,                -- Token contract address
  asset_name TEXT,                    -- "USDC", "USDT"
  pay_to TEXT NOT NULL,               -- Recipient wallet address
  max_amount_required TEXT,           -- Price in smallest token unit
  price_usd DECIMAL(20,6),            -- Calculated USD price
  max_timeout_seconds INTEGER,

  -- API schema (from outputSchema)
  output_schema JSONB,                -- Full outputSchema object
  extra JSONB,                        -- Extra metadata

  -- Additional fields
  mime_type TEXT,                     -- Response content type
  channel TEXT,                       -- Payment channel
  discoverable BOOLEAN,               -- From inputSchema.discoverable

  -- Timestamps
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW(),

  UNIQUE(resource_id, network, asset)
);
```

### Resource Tags Table

Maps resources to auto-detected tags.

```sql
CREATE TABLE resource_tags (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  resource_id UUID REFERENCES resources(id) ON DELETE CASCADE,
  tag_id UUID REFERENCES tags(id) ON DELETE CASCADE,
  UNIQUE(resource_id, tag_id)
);

CREATE TABLE tags (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT UNIQUE NOT NULL,
  category TEXT,                      -- Tag category grouping
  created_at TIMESTAMPTZ DEFAULT NOW()
);
```

## Data Fields Explained

### Facilitator Response Structure

```json
{
  "items": [
    {
      "resource": "https://api.example.com/weather",
      "type": "http",
      "x402Version": 1,
      "lastUpdated": "2025-12-25T10:00:00Z",
      "method": "POST",

      "metadata": {
        "description": "Weather API",
        "input": { "city": "San Francisco" },
        "output": { "temperature": 72, "conditions": "sunny" },
        "inputSchema": {
          "type": "object",
          "properties": {
            "city": { "type": "string", "description": "City name" }
          },
          "required": ["city"]
        },
        "outputSchema": {
          "type": "object",
          "properties": {
            "temperature": { "type": "number" },
            "conditions": { "type": "string" }
          }
        }
      },

      "accepts": [
        {
          "scheme": "exact",
          "network": "base",
          "asset": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
          "payTo": "0x1234567890abcdef...",
          "maxAmountRequired": "100000",
          "maxTimeoutSeconds": 60,
          "mimeType": "application/json",
          "channel": "default",
          "outputSchema": {
            "input": {
              "method": "POST",
              "type": "http",
              "discoverable": true,
              "bodyFields": {
                "city": { "type": "string", "required": true }
              }
            },
            "output": {
              "temperature": { "type": "number" },
              "conditions": { "type": "string" }
            }
          },
          "extra": {
            "name": "USD Coin",
            "version": "2"
          }
        }
      ]
    }
  ]
}
```

### Field Mapping

| Facilitator Field | Database Column | Table |
|-------------------|-----------------|-------|
| `item.resource` | `resource` | resources |
| `item.type` | `type` | resources |
| `item.x402Version` | `x402_version` | resources |
| `item.method` | `method` | resources |
| `item.lastUpdated` | `last_updated` | resources |
| `item.metadata` | `metadata` | resources |
| `item.inputSchema` | `input_schema` | resources |
| `item.outputSchema` | `item_output_schema` | resources |
| `item.metadata.input` | `example_input` | resources |
| `item.metadata.output` | `example_output` | resources |
| `item.metadata.inputSchema` | `input_schema_v2` | resources |
| `item.metadata.outputSchema` | `output_schema_v2` | resources |
| `item.category` or `metadata.category` | `self_reported_category` | resources |
| `item.tags` or `metadata.tags` | `self_reported_tags` | resources |
| `accept.scheme` | `scheme` | accepts |
| `accept.network` | `network` | accepts |
| `accept.asset` | `asset` | accepts |
| `accept.payTo` | `pay_to` | accepts |
| `accept.maxAmountRequired` | `max_amount_required` | accepts |
| `accept.maxTimeoutSeconds` | `max_timeout_seconds` | accepts |
| `accept.mimeType` | `mime_type` | accepts |
| `accept.channel` | `channel` | accepts |
| `accept.outputSchema` | `output_schema` | accepts |
| `accept.outputSchema.input.discoverable` | `discoverable` | accepts |
| `accept.extra` | `extra` | accepts |

## Auto-Tagging System

Resources are automatically tagged based on URL and description keywords:

| Tag | Keywords |
|-----|----------|
| `ai_agent` | agent, swarm, autonomous, eliza, virtuals |
| `llm_inference` | llm, gpt, claude, gemini, inference, chat |
| `blockchain_data` | onchain, token-info, dex-data, transaction |
| `trading` | trade, swap, dex, exchange, price |
| `nft` | nft, collectible, mint, opensea |
| `payment` | payment, transfer, usdc, wallet |
| `social_media` | twitter, social, farcaster, lens |
| `developer_tools` | sdk, api, webhook, rpc |
| `content` | media, image, video, generate-image |
| `security` | security, audit, verify, kyc |
| `other` | (default if no match) |

## Filtering Rules

### Testnet Networks (Excluded)

Networks containing these patterns are filtered out:
- `*-sepolia`, `*-testnet`, `goerli`, `mumbai`, `holesky`, `*-devnet`

### Hosting Domains (Excluded)

Resources on these hosting platforms are filtered:
- `vercel.app`, `netlify.app`, `render.com`, `herokuapp.com`, `railway.app`, `replit.dev`, `ngrok.io`, `localhost`

## Price Calculation

USD price is calculated from `maxAmountRequired` based on token decimals:

```python
# USDC has 6 decimals
price_usd = int(max_amount_required) / 1_000_000
```

## Pipeline Execution

### Schedule
- **Frequency**: Hourly via Cloud Scheduler
- **Runtime**: Google Cloud Run Job
- **Duration**: ~30-40 minutes

### Steps
1. Fetch from all 6 facilitators (paginated)
2. Deduplicate by resource URL (keep newest)
3. Filter testnet networks and hosting domains
4. Upsert to Supabase (origins → resources → accepts)
5. Scrape metadata for new origins
6. Auto-tag resources
7. Sync on-chain traction data (if API keys configured)

### Environment Variables

```bash
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_KEY=your-service-key
ALCHEMY_API_KEY=your-alchemy-key      # Optional: for Base traction
HELIUS_API_KEY=your-helius-key        # Optional: for Solana traction
```

## API Usage Examples

### Query All Origins with Resources

```sql
SELECT
  o.domain,
  o.title,
  o.total_transactions,
  o.total_volume_usd,
  COUNT(r.id) as resource_count
FROM origins o
LEFT JOIN resources r ON r.origin_id = o.id
GROUP BY o.id
ORDER BY o.total_volume_usd DESC NULLS LAST;
```

### Find Resources by Tag

```sql
SELECT r.resource, r.description, r.method, a.price_usd, a.network
FROM resources r
JOIN resource_tags rt ON rt.resource_id = r.id
JOIN tags t ON t.id = rt.tag_id
JOIN accepts a ON a.resource_id = r.id
WHERE t.name = 'llm_inference'
ORDER BY a.price_usd ASC;
```

### Get Resources with v2 Metadata

```sql
SELECT
  r.resource,
  r.description,
  r.example_input,
  r.example_output,
  r.input_schema_v2,
  r.self_reported_category,
  r.self_reported_tags
FROM resources r
WHERE r.example_input IS NOT NULL
   OR r.input_schema_v2 IS NOT NULL;
```

### Get Top Origins by Volume

```sql
SELECT
  domain,
  title,
  total_transactions,
  total_volume_usd,
  unique_buyers,
  resource_count
FROM origins
WHERE total_volume_usd > 0
ORDER BY total_volume_usd DESC
LIMIT 20;
```

## Related Files

| File | Purpose |
|------|---------|
| `fetch_discovery.py` | Main pipeline script |
| `backfill_missing_fields.py` | Backfill legacy fields |
| `backfill_v2_metadata.py` | Backfill v2 Bazaar metadata |
| `cleanup_dead_origins.py` | Remove inactive origins |
| `Dockerfile` | Container image definition |

## x402 Protocol Reference

- **Specification**: https://x402.org
- **Coinbase CDP Docs**: https://docs.cdp.coinbase.com/x402
- **Bazaar Discovery**: https://docs.cdp.coinbase.com/x402/bazaar
- **GitHub**: https://github.com/coinbase/x402
