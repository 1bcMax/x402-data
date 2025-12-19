# x402-data

x402 生态数据采集与分析。

## 数据采集

每小时自动采集 6 个 facilitator 的服务发现数据：

- CDP Coinbase
- PayAI
- Questflow
- AnySpend
- AurraCloud
- Thirdweb

### 运行方式

```bash
# 本地测试
python fetch_discovery.py

# Cloud Run Job (已部署)
gcloud run jobs execute fetch-x402-discovery --region=us-central1
```

### 数据存储

```
gs://blockrun-data/discovery/
├── discovery_2025-12-19_05.json
├── discovery_2025-12-19_06.json
└── ...
```

## Facilitator 地址

`facilitators_addresses.json` 包含 22 个 facilitator 的链上地址：
- 92 个 EVM 地址 (Base, Polygon, Arbitrum)
- 12 个 Solana 地址

用于后续 BigQuery 链上数据分析。

## 部署

```bash
# 部署 Cloud Run Job
gcloud run jobs deploy fetch-x402-discovery \
  --source=. \
  --region=us-central1 \
  --set-env-vars="GCS_BUCKET=blockrun-data"

# 设置定时任务 (已配置: 每小时)
gcloud scheduler jobs list --location=us-central1
```
