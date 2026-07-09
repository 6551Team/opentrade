# OpenTrade Skills

> Trade CEX & DEX from a single unified API — powered by the [6551 platform](https://www.newsliquid.com/mcp).

---

## Overview

OpenTrade provides **7 specialized skills** covering the full spectrum of blockchain trading:

| # | Skill | Description |
|---|-------|-------------|
| 1 | [opentrade-cex](#-opentrade-cex) | CEX spot & futures trading across 5 exchanges, with built-in risk controls |
| 2 | [opentrade-dex-swap](#-opentrade-dex-swap) | DEX swap operations (quote, swap, approve, liquidity) |
| 3 | [opentrade-market](#-opentrade-market) | Market data (prices, K-line, trades, smart money signals) |
| 4 | [opentrade-token](#-opentrade-token) | Token information (search, info, holders, trending) |
| 5 | [opentrade-portfolio](#-opentrade-portfolio) | Wallet / Portfolio (balances, portfolio value, transaction history) |
| 6 | [opentrade-wallet](#-opentrade-wallet) | Custodial wallet management (BSC & Solana) |
| 7 | [opentrade-gateway](#-opentrade-gateway) | Transaction management (gas, simulation, broadcast, tracking) |

---

## Quick Start

**1. Get your API token** at https://www.newsliquid.com/mcp

**2. Set environment variable**

```bash
export OPENNEWS_TOKEN="your_token_here"
```

Or add to your `.env` file:

```
OPENNEWS_TOKEN=your_token_here
```

## Query Routing Priority

For user-specific holdings/assets, positions, orders, closed orders, or trade history, query **CEX first** with `opentrade-cex`.

Only fall back to DEX/on-chain skills when the CEX query returns no relevant data, or when the user explicitly asks for wallet, DEX, or on-chain data:

| User asks for | First | Fallback if no CEX data |
|---|---|---|
| Holdings / assets / positions | `opentrade-cex` | `opentrade-portfolio` |
| Open / closed orders | `opentrade-cex` | `opentrade-gateway` only for on-chain broadcast order status |
| Trade / transaction history | `opentrade-cex` | `opentrade-market` for public on-chain token trade logs |

---

## 📈 opentrade-cex

Unified CEX trading engine — trade spot & perpetual futures across **5 major exchanges** through a single API. All orders execute server-side with a built-in 4-layer risk engine. No private key management or transaction signing required.

### Supported Exchanges

| Exchange | Spot | Perpetual | Highlights |
|----------|:----:|:--------------:|------------|
| **Binance** | ✅ | ✅ | Full TP/SL, hedge mode, OCO |
| **Bybit** | ✅ | ✅ | Linear perpetual, hedge mode |
| **OKX** | ✅ | ✅ | Swap/futures, OCO orders |
| **Hyperliquid** | ✅ | ✅ | On-chain perpetual DEX, wallet agent |
| **Aster** | ✅ | ✅ | On-chain DEX, wallet agent |

### 29 API Endpoints

| Category | Endpoints | Capabilities |
|----------|:---------:|--------------|
| **Market Data** | 5 | Real-time ticker, K-line/candlestick, unified metadata across exchanges, base currency discovery, server time |
| **Account** | 3 | Balance summary (spot/swap/future/margin), spot asset query with FIFO cost basis, batch asset listing |
| **Config** | 2 | Read/update default exchange, leverage, position size, encrypted exchange credentials |
| **Orders** | 5 | Place / edit / cancel orders, list open & closed. 7 order types: `market` `limit` `stop_market` `stop_limit` `take_profit_market` `take_profit_limit` `oco` |
| **Positions** | 3 | Current positions, historical positions with trade details, close (full or partial) |
| **Leverage & Margin** | 6 | Leverage tiers, get/set leverage, margin mode (cross/isolated), position mode (one-way/hedge) |
| **Wallet Agent** | 4 | Create, list, query, authorize Ethereum-compatible agent wallets for Hyperliquid & Aster |

### Advanced Trading Features

| Feature | Description |
|---------|-------------|
| TP/SL Attachment | Attach Take-Profit / Stop-Loss directly to any order |
| Flexible Sizing | Place orders by base quantity (e.g. `0.1 BTC`) or quote amount (e.g. `100 USDT`) |
| Hedge Mode | Hold long and short positions simultaneously |
| Auto Normalization | Automatic precision & contract size normalization across exchanges |
| Cost Tracking | Weighted average cost (FIFO) calculation for spot positions |

### Multi-Layer Risk Engine

All write operations (place order, edit order, close position, set leverage) pass through a configurable risk engine before execution. **4 core layers are enabled by default**, with 10+ additional optional layers available:

```
Request → [Rate Limit] → [Price Deviation] → [Position Limit] → [Balance Check] → Exchange
```

**Core Risk Layers** (enabled by default):

| Layer | Rule | Default Threshold | Description |
|:-----:|------|-------------------|-------------|
| 1 | Rate Limit | 30 requests / minute | Prevents API abuse and exchange rate limits |
| 2 | Price Deviation | Max 10% from market | Rejects orders too far from current market price |
| 3 | Position Size Limit | Single: 20%, Total: 80% | Limits single position and total exposure |
| 4 | Balance Check | Min 5% reserve | Ensures minimum balance reserve remains |

**Optional Risk Layers** (configurable):

| Layer | Description | Key Parameters |
|-------|-------------|----------------|
| Drawdown Limit | Prevents excessive losses over time periods | Daily: 5%, Weekly: 10%, Monthly: 20% |
| Liquidation Prevention | Auto-reduces positions approaching liquidation | Margin call ratio: 150%, Liquidation ratio: 110% |
| Circuit Breaker | Halts trading during extreme market volatility | Cooldown: 15 min, Price change thresholds |
| Slippage Protection | Rejects orders with excessive slippage | Max spread: 0.5%, Max slippage: 1% |
| Volatility Adjustment | Reduces position size during high volatility | Adjusts limits based on market conditions |
| Order Anomaly Detection | Detects unusual order patterns (fat finger) | Max order value: $100k, Size std threshold: 3σ |
| Anti-Manipulation | Prevents wash trading and self-trading | Order-to-trade ratio: 10:1, Self-trade window: 100ms |
| Time Restriction | Limits trading to specific hours or blackout periods | Max holding days: 30, Restricted hours, News blackout |

### Security

- Exchange API credentials are **encrypted at rest** — never stored in plain text
- All trades execute **server-side** — no client-side key exposure
- Wallet agent uses **ECDSA delegated authorization** for on-chain exchanges

---

## 🔄 opentrade-dex-swap

Execute DEX swaps across multiple chains.

| Feature | Description |
|---------|-------------|
| Swap Quotes | Get real-time quotes from multiple DEX sources |
| Swap Execution | Generate ready-to-sign swap transactions |
| Token Approval | Approve ERC-20 token spending |
| Chain & Source Discovery | Query supported chains and liquidity sources |
| History | View swap history |

---

## 📊 opentrade-market

Access comprehensive on-chain market data.

| Feature | Description |
|---------|-------------|
| Token Prices | Single or batch price queries |
| K-line / Candlestick | Historical OHLCV data |
| Recent Trades | Latest trade activity |
| Smart Money Signals | Monitor KOL / whale / smart money movements |
| Index Prices | Aggregated index pricing |

---

## 🪙 opentrade-token

Discover and analyze tokens.

| Feature | Description |
|---------|-------------|
| Search | Find tokens by name, symbol, or contract address |
| Token Info | Get token basic info and metadata |
| Holders | View holder distribution |
| Trending | Find trending tokens |

---

## 💰 opentrade-portfolio

Query on-chain portfolio balances after CEX fallback, or when the user explicitly asks for wallet/DEX/on-chain balances.

| Feature | Description |
|---------|-------------|
| Token Balances | Single, batch, or all token balances |
| Portfolio Value | Calculate total portfolio value in USD |
| Transaction History | View on-chain transaction history |

---

## 🔐 opentrade-wallet

Manage custodial wallets powered by [Turnkey](https://www.turnkey.com/). **Supports BSC and Solana only.**

| Feature | Description |
|---------|-------------|
| Create Wallet | Generate custodial wallet (BSC + Solana addresses) |
| Account Info | Query custodial account details |
| DEX Swap | Execute swap with auto-sign + broadcast |
| Withdraw | Withdraw native tokens (BNB / SOL) |

**Security Architecture:**
- Private keys managed by **Turnkey + AWS KMS** — 6551 never stores your keys
- Wallets are **non-extractable** — no one can export the raw private key

> **Note**: Newly created wallets have zero balance. Deposit **BNB** (BSC) or **SOL** (Solana) before trading. Do NOT send tokens from other chains — funds will be lost.

---

## 📡 opentrade-gateway

Manage blockchain transactions end-to-end.

| Feature | Description |
|---------|-------------|
| Gas Prices | Get current gas prices |
| Gas Estimation | Estimate gas limits for transactions |
| Simulation | Dry-run transactions before broadcasting |
| Broadcast | Send signed transactions on-chain |
| Tracking | Track order / transaction status |

---

## Error Handling

| Code | Description |
|:----:|-------------|
| `200` | Success |
| `400` | Bad request — invalid parameters |
| `401` | Unauthorized — invalid or missing token |
| `403` | Forbidden — insufficient permissions |
| `404` | Not found |
| `429` | Rate limit exceeded |
| `500` | Internal server error |
| `503` | Service unavailable |

## Best Practices

1. **Simulate transactions** before broadcasting
2. **Use batch queries** when fetching multiple token prices
3. **Implement retry logic** with exponential backoff
4. **Monitor smart money signals** for market insights
5. **Cache token info** to reduce API calls
6. **Use appropriate slippage** for volatile markets

---

## Support

- **Platform**: https://www.newsliquid.com
- **API Token**: https://www.newsliquid.com/mcp

## License

See main project [LICENSE](LICENSE) file.
