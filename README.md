# x402 Discovery Data Pipeline

This is the primary data pipeline for BlockRun. It collects x402 service discovery data from all facilitators and syncs it to Supabase.

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                         DATA FLOW                                     │
├──────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  6 Facilitators ──► fetch-x402-discovery ──► GCS (raw JSON)          │
│  (hourly)            (Cloud Run Job)          gs://blockrun-data/     │
│                                                      │                │
│                                                      ▼                │
│                                              blockrun/scripts/        │
│                                              sync_discovery.py        │
│                                                      │                │
│                                                      ▼                │
│  Alchemy + Helius ──► sync_traction.py ──────► Supabase              │
│  (on-chain data)      (local script)           (origins, resources,  │
│                                                 accepts tables)       │
└──────────────────────────────────────────────────────────────────────┘
```

**Raw data** is stored in GCS (source of truth).
**Processed data** is synced to Supabase for website queries.

## Pipeline Jobs

| Job | Schedule | Description | Output |
|-----|----------|-------------|--------|
| `fetch-x402-discovery` | Hourly | Fetches from 6 facilitators, filters testnet/hosting | GCS JSON files |
| `sync_discovery.py` | Manual | Reads GCS files, upserts to Supabase | Supabase tables |
| `sync_traction.py` | Manual | Fetches on-chain USDC transfers | Supabase origins.total_* |

### GCP Resources

| Resource | Name | Location |
|----------|------|----------|
| Cloud Run Job | `fetch-x402-discovery` | us-central1 |
| Cloud Scheduler | `fetch-x402-hourly` | us-central1 |
| GCS Bucket | `gs://blockrun-data/discovery/` | - |

### Environment Variables (Cloud Run)

- `GCS_BUCKET=blockrun-data`

## Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                      HOURLY CLOUD RUN JOB                           │
│                                                                     │
│  ┌───────────────┐    ┌───────────────┐    ┌───────────────┐       │
│  │ CDP Coinbase  │    │    PayAI      │    │   Questflow   │       │
│  │  /discovery   │    │  /discovery   │    │  /discovery   │       │
│  └───────┬───────┘    └───────┬───────┘    └───────┬───────┘       │
│          │                    │                    │                │
│          └──────────┬─────────┴─────────┬──────────┘                │
│                     ▼                   ▼                           │
│  ┌───────────────┐    ┌───────────────┐    ┌───────────────┐       │
│  │   AnySpend    │    │  AurraCloud   │    │   Thirdweb    │       │
│  │  /discovery   │    │  /discovery   │    │  /discovery   │       │
│  └───────┬───────┘    └───────┬───────┘    └───────┬───────┘       │
│          │                    │                    │                │
│          └──────────┬─────────┴─────────┬──────────┘                │
│                     ▼                                               │
│             ┌───────────────┐                                       │
│             │ Filter Testnet│  <-- Remove *-sepolia, goerli, etc.   │
│             └───────┬───────┘                                       │
│                     ▼                                               │
│             ┌───────────────┐                                       │
│             │  Deduplicate  │  <-- By resource URL                  │
│             └───────┬───────┘                                       │
│                     ▼                                               │
│             ┌───────────────┐                                       │
│             │    Upsert     │  origins → resources → accepts        │
│             │   Supabase    │                                       │
│             └───────┬───────┘                                       │
│                     ▼                                               │
│             ┌───────────────┐                                       │
│             │ Scrape New    │  title, description, og:image,        │
│             │   Origins     │  favicon, twitter, discord, github    │
│             └───────────────┘                                       │
└─────────────────────────────────────────────────────────────────────┘
                              ▼
                    ┌─────────────────┐
                    │    Supabase     │
                    │    Database     │
                    │  ┌───────────┐  │
                    │  │  origins  │  │
                    │  ├───────────┤  │
                    │  │ resources │  │
                    │  ├───────────┤  │
                    │  │  accepts  │  │
                    │  ├───────────┤  │
                    │  │   tags    │  │
                    │  └───────────┘  │
                    └─────────────────┘
                              ▼
                    ┌─────────────────┐
                    │ BlockRun Website│
                    │   /discover     │
                    └─────────────────┘
```

## Pipeline Steps

### 1. Fetch from Facilitators

The pipeline fetches from 6 x402 facilitator discovery endpoints:

| Facilitator | Endpoint |
|-------------|----------|
| CDP Coinbase | `https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources` |
| PayAI | `https://facilitator.payai.network/discovery/resources` |
| Questflow | `https://facilitator.questflow.ai/discovery/resources` |
| AnySpend | `https://mainnet.anyspend.com/x402/discovery/resources` |
| AurraCloud | `https://x402-facilitator.aurracloud.com/discovery/resources` |
| Thirdweb | `https://api.thirdweb.com/v1/payments/x402/discovery/resources` |

Each facilitator is fetched with pagination (100 items per page) and rate limiting protection (exponential backoff on 429).

### 2. Filter Testnet Networks

Remove all testnet payment options from `accepts` array. If an item has no mainnet payment options left, it's excluded entirely.

**Filtered testnet patterns** (see `TESTNET_PATTERNS` in fetch_discovery.py):
- `*-sepolia` (e.g., base-sepolia)
- `*-testnet`
- `goerli`
- `mumbai`
- `holesky`
- `*-devnet`

### 2.5. Filter Hosting Domains

Skip resources from temporary hosting platforms (not serious production services).

**Filtered hosting domains** (see `HOSTING_DOMAINS` in fetch_discovery.py):
- `.vercel.app`, `.netlify.app`, `.render.com`, `.onrender.com`
- `.herokuapp.com`, `.railway.app`, `.fly.dev`, `.replit.dev`
- `.glitch.me`, `.surge.sh`, `.pages.dev`, `.workers.dev`
- `.nx.link`, `localhost`

### 3. Deduplicate Resources

Resources are deduplicated by their full URL. When duplicates are found across facilitators, we keep the one with the newest `lastUpdated` timestamp.

### 4. Upsert to Supabase

Data is upserted to Supabase in this order:

1. **Origins** - Domain-level records (e.g., `lucyos.ai`)
2. **Resources** - Individual API endpoints (e.g., `https://lucyos.ai/api/chat`)
3. **Accepts** - Payment options for each resource (network, asset, price)
4. **Resource Tags** - Auto-detected tags based on keywords

### 5. Scrape New Origins (Inline)

For newly discovered origins, the scraper immediately fetches metadata:

| Field | Source |
|-------|--------|
| `title` | `<title>` or `og:title` |
| `description` | `<meta name="description">` or `og:description` |
| `favicon` | `<link rel="icon">` |
| `og_image` | `og:image` meta tag |
| `twitter` | Links containing `twitter.com/` or `x.com/` |
| `discord` | Links containing `discord.gg/` or `discord.com/` |
| `github` | Links containing `github.com/` |

## Database Schema

### origins
```sql
CREATE TABLE origins (
  id UUID PRIMARY KEY,
  origin TEXT NOT NULL UNIQUE,      -- https://lucyos.ai
  domain TEXT NOT NULL,             -- lucyos.ai
  title TEXT,
  description TEXT,
  favicon TEXT,
  og_image TEXT,
  twitter TEXT,                     -- @handle
  discord TEXT,                     -- invite URL
  github TEXT,                      -- org/repo
  submitted_by TEXT,
  verified BOOLEAN DEFAULT FALSE,
  featured BOOLEAN DEFAULT FALSE,
  resource_count INTEGER DEFAULT 0,
  created_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ
);
```

### resources
```sql
CREATE TABLE resources (
  id UUID PRIMARY KEY,
  origin_id UUID REFERENCES origins(id),
  resource TEXT NOT NULL UNIQUE,    -- Full URL
  path TEXT NOT NULL,               -- Just the path
  type TEXT DEFAULT 'http',
  x402_version INTEGER DEFAULT 1,
  method TEXT DEFAULT 'POST',
  description TEXT,
  mime_type TEXT,
  metadata JSONB,
  last_updated TIMESTAMPTZ,
  created_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ
);
```

### accepts
```sql
CREATE TABLE accepts (
  id UUID PRIMARY KEY,
  resource_id UUID REFERENCES resources(id),
  scheme TEXT DEFAULT 'exact',
  network TEXT NOT NULL,            -- base, solana, etc.
  asset TEXT NOT NULL,              -- Token contract address
  asset_name TEXT,                  -- USDC
  pay_to TEXT NOT NULL,             -- Recipient wallet
  max_amount_required TEXT,         -- In token's smallest unit
  price_usd DECIMAL(20, 6),         -- Converted to USD
  max_timeout_seconds INTEGER,
  output_schema JSONB,
  extra JSONB,
  created_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ,
  UNIQUE(resource_id, scheme, network)
);
```

## Auto-Tagging

Resources are automatically tagged based on URL and description keywords:

| Tag | Keywords |
|-----|----------|
| AI | ai, llm, gpt, claude, gemini, ml, model, chat, completion, inference |
| Trading | trade, trading, swap, dex, exchange, price, market |
| Blockchain | blockchain, web3, nft, token, wallet, contract, eth, sol |
| Search | search, query, find, lookup, index |
| Data | data, api, fetch, scrape, crawl |
| Utility | (default if no other tags match) |

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SUPABASE_URL` | Yes | Supabase project URL |
| `SUPABASE_SERVICE_KEY` | Yes | Supabase service role key (bypasses RLS) |
| `GOOGLE_CLOUD_PROJECT` | No | For GCS fallback (legacy) |
| `GCS_BUCKET` | No | For GCS fallback (legacy) |

## Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Create .env file
cat > .env << EOF
SUPABASE_URL=https://fipgpddebmfytowkurvb.supabase.co
SUPABASE_SERVICE_KEY=your-service-role-key
EOF

# Run locally
python fetch_discovery.py
```

## Deployment to Cloud Run

### Build and Push

```bash
# Set project
gcloud config set project avian-voice-476622-r8

# Build image
docker build -t gcr.io/avian-voice-476622-r8/x402-discovery .

# Push to GCR
docker push gcr.io/avian-voice-476622-r8/x402-discovery
```

### Create Cloud Run Job

```bash
gcloud run jobs create x402-discovery-sync \
  --image gcr.io/avian-voice-476622-r8/x402-discovery \
  --region us-central1 \
  --set-env-vars "SUPABASE_URL=https://fipgpddebmfytowkurvb.supabase.co" \
  --set-secrets "SUPABASE_SERVICE_KEY=supabase-service-key:latest" \
  --memory 512Mi \
  --task-timeout 10m
```

### Schedule Hourly

```bash
gcloud scheduler jobs create http x402-discovery-hourly \
  --location us-central1 \
  --schedule "0 * * * *" \
  --uri "https://us-central1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/avian-voice-476622-r8/jobs/x402-discovery-sync:run" \
  --http-method POST \
  --oauth-service-account-email avian-voice-476622-r8@appspot.gserviceaccount.com
```

### Manual Run

```bash
gcloud run jobs execute x402-discovery-sync --region us-central1
```

## Monitoring

### Check Sync History

```sql
SELECT
  id,
  started_at,
  completed_at,
  new_origins,
  new_resources,
  new_accepts,
  errors
FROM sync_history
ORDER BY created_at DESC
LIMIT 10;
```

### Check Origin Counts

```sql
SELECT
  COUNT(*) as total_origins,
  SUM(CASE WHEN verified THEN 1 ELSE 0 END) as verified,
  SUM(resource_count) as total_resources
FROM origins;
```

### Check Network Distribution

```sql
SELECT
  network,
  COUNT(*) as count
FROM accepts
GROUP BY network
ORDER BY count DESC;
```

## Troubleshooting

### "Warning: supabase not installed"
```bash
pip install supabase>=2.0.0
```

### "Warning: beautifulsoup4 not installed"
```bash
pip install beautifulsoup4>=4.12.0 lxml>=5.0.0
```

### Rate Limited by Facilitator
The script has exponential backoff built in. If persistent, increase sleep time in `fetch_with_pagination()`.

### Origin Scraping Fails
Some origins may block scraping or have invalid SSL. The script catches all errors and continues. Check logs for `Failed to scrape {domain}`.

## On-Chain Traction Sync

The `sync_traction.py` script fetches USDC transfer data to calculate traction metrics for each origin.

### Data Sources
| Chain | API | Free Tier |
|-------|-----|-----------|
| Base | Alchemy | 300M CUs/month |
| Solana | Helius | 1M credits/month |

### Metrics Stored (in origins table)
| Column | Description |
|--------|-------------|
| `total_transactions` | Count of USDC transfers to pay_to addresses |
| `total_volume_usd` | Sum of USDC transferred |
| `unique_buyers` | Count of distinct sender addresses |
| `last_transaction_at` | Timestamp of most recent transfer |
| `traction_updated_at` | When metrics were last synced |

### Running Traction Sync
```bash
# Requires ALCHEMY_API_KEY and HELIUS_API_KEY in .env
source /path/to/venv/bin/activate
python sync_traction.py
```

## Files

| File | Description |
|------|-------------|
| `fetch_discovery.py` | Main discovery pipeline (Cloud Run job) |
| `sync_traction.py` | On-chain traction metrics sync |
| `requirements.txt` | Python dependencies |
| `Dockerfile` | Container image for fetch_discovery.py |
| `facilitators_addresses.json` | On-chain addresses for BigQuery analysis |
| `.env` | Local environment variables (not in git) |

## Database Tables

| Table | Description | Updated By |
|-------|-------------|------------|
| `origins` | Domain-level service records | fetch_discovery.py, sync_traction.py |
| `resources` | Individual API endpoints | fetch_discovery.py |
| `accepts` | Payment options (network, price, pay_to) | fetch_discovery.py |
| `tags` | Categories for filtering | Manual seed |
| `resource_tags` | Many-to-many resource↔tag | fetch_discovery.py |
| `sync_history` | Audit log of sync runs | fetch_discovery.py |
| `discovery_events` | Historical discovery snapshots | (optional) |

## Supabase Project

| Field | Value |
|-------|-------|
| Project ID | `fipgpddebmfytowkurvb` |
| URL | `https://fipgpddebmfytowkurvb.supabase.co` |
| Region | - |
| Schema | See `blockrun/supabase/schema.sql` |
