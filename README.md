# OpenTrade Skills

A collection of skills for interacting with CEX and DEX aggregator APIs via the 6551 platform.

## Overview

OpenTrade provides 7 specialized skills for comprehensive blockchain trading operations:

1. **opentrade-dex-swap** - DEX swap operations (quote, swap, approve, liquidity)
2. **opentrade-transaction** - Transaction management (gas, simulation, broadcast, tracking)
3. **opentrade-portfolio** - Wallet / Portfolio operations (balances, portfolio value, transaction history)
4. **opentrade-market** - Market data (prices, K-line, trades, smart money signals)
5. **opentrade-token** - Token information (search, info, holders, trending)
6. **opentrade-wallet** - Custodial wallet management (create wallet, get account, swap, withdraw). Supports BSC and Solana only.
7. **opentrade-cex** - CEX trading (spot & futures orders, positions, leverage, account management, wallet agent). Server-side execution with built-in risk controls.

## Quick Start

### 1. Get Your API Token

Visit https://6551.io/mcp to obtain your API token.

### 2. Set Environment Variable

```bash
export OPEN_TOKEN="your_token_here"
```

Or add to your `.env` file:

```bash
OPEN_TOKEN=your_token_here
```

## Skills Overview

### 🔄 opentrade-dex-swap

Execute DEX swaps across multiple chains.

**Key Features:**
- Get swap quotes
- Generate swap transactions
- Approve ERC-20 tokens
- Query supported chains and liquidity sources
- View swap history

### 📡 opentrade-transaction

Manage blockchain transactions end-to-end.

**Key Features:**
- Get current gas prices
- Estimate gas limits
- Simulate transactions (dry-run)
- Broadcast signed transactions
- Track order status

### 💰 opentrade-portfolio

Query portfolio balances and transaction history.

**Key Features:**
- Get token balances (single or batch)
- Get all token balances
- Calculate total portfolio value
- View transaction history

### 📊 opentrade-market

Access comprehensive market data.

**Key Features:**
- Get token prices (single or batch)
- View K-line / candlestick data
- Get recent trades
- Monitor smart money / KOL / whale signals
- Get index prices

### 🪙 opentrade-token

Discover and analyze tokens.

**Key Features:**
- Search tokens by name/symbol/address
- Get token basic info
- View holder distribution
- Find trending tokens

### 🔐 opentrade-wallet

Manage custodial wallets powered by [Turnkey](https://www.turnkey.com/). **Only supports BSC and Solana networks.**

**Security Architecture:**
- Private keys are managed by **Turnkey** with **AWS KMS** (Key Management Service) as delegated custody
- **6551 does NOT store your private keys** — all signing operations are performed within Turnkey's secure infrastructure backed by AWS KMS
- Your wallet is non-extractable: no one (including 6551) can export the raw private key

**Key Features:**
- Create custodial wallet (BSC + Solana addresses)
- Get custodial account info
- Execute DEX swap (auto-sign + broadcast)
- Withdraw native tokens (BNB on BSC, SOL on Solana)

> **Note**: Newly created wallets have zero balance. You must deposit **BNB** (BSC network) or **SOL** (Solana network) before trading. Do NOT send tokens from other chains — funds will be lost.

### 📈 opentrade-cex

Unified CEX (centralized exchange) trading engine — trade spot & perpetual futures across 5 major exchanges through a single API. All orders execute server-side with a built-in 4-layer risk engine. No private key management or transaction signing required.

**Supported Exchanges:**

| Exchange | Spot | USDT Perpetual | Features |
|----------|------|----------------|----------|
| Binance | ✅ | ✅ | Full TP/SL, hedge mode, OCO |
| Bybit | ✅ | ✅ | Linear perpetual, hedge mode |
| OKX | ✅ | ✅ | Swap/futures, OCO orders |
| Hyperliquid | ✅ | ✅ | On-chain perpetual DEX, wallet agent |
| Aster | ✅ | ✅ | On-chain DEX, wallet agent |

**29 API Endpoints across 7 categories:**

- **Market Data** (5 endpoints) — Real-time ticker, K-line/candlestick, unified market metadata across exchanges, base currency discovery, server time
- **Account** (3 endpoints) — Balance summary (spot/swap/future/margin), single spot asset query with FIFO cost basis, batch spot asset listing
- **Config** (2 endpoints) — Read/update default exchange, leverage, position size, and encrypted exchange credentials
- **Orders** (5 endpoints) — Place/edit/cancel orders, list open & closed orders. Supports 7 order types: `market`, `limit`, `stop_market`, `stop_limit`, `take_profit_market`, `take_profit_limit`, `oco`
- **Positions** (3 endpoints) — View current positions, historical positions with trade details, close positions (full or partial)
- **Leverage & Margin** (6 endpoints) — Leverage tiers, get/set leverage, margin mode (cross/isolated), position mode (one-way/hedge)
- **Wallet Agent** (4 endpoints) — Create, list, query, and authorize Ethereum-compatible agent wallets for Hyperliquid & Aster

**Advanced Trading Features:**
- Attach **Take-Profit / Stop-Loss** directly to any order
- Place orders by **base quantity** (e.g., 0.1 BTC) or **quote amount** (e.g., 100 USDT)
- **Hedge mode** — hold long and short positions simultaneously
- Automatic **precision & contract size** normalization across exchanges
- **Weighted average cost** calculation for spot positions

**4-Layer Risk Engine** (protects all write operations):

| Layer | Rule | Default Threshold |
|-------|------|-------------------|
| 1 | Rate Limit | 30 requests/minute |
| 2 | Price Deviation | Max 10% from market price |
| 3 | Position Size Limit | Single: 20%, Total: 80% of balance |
| 4 | Balance Check | Min 5% balance reserve |

**Security:**
- Exchange API credentials are **encrypted at rest** — never stored in plain text
- All trades execute **server-side** — no client-side key exposure
- Wallet agent uses **ECDSA delegated authorization** for on-chain exchanges

## Error Handling

Common error codes:

| Code | Description |
|------|-------------|
| 200 | Success |
| 400 | Bad request (invalid parameters) |
| 401 | Unauthorized (invalid or missing token) |
| 403 | Forbidden (insufficient permissions) |
| 404 | Not found |
| 429 | Rate limit exceeded |
| 500 | Internal server error |
| 503 | Service unavailable |

## Rate Limits

- Each request consumes 1 quota unit
- Rate limits depend on your subscription plan
- Check your remaining quota in the response headers

## Best Practices

1. **Simulate transactions** before broadcasting
2. **Use batch queries** when fetching multiple token prices
3. **Implement retry logic** with exponential backoff
4. **Monitor smart money signals** for market insights
5. **Cache token info** to reduce API calls
6. **Use appropriate slippage** for volatile markets

## Support

- Platform: https://6551.io
- API Token: https://6551.io/mcp

## License

See main project LICENSE file.
