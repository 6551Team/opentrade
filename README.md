# OpenTrade Skills

A collection of skills for interacting with Newsliquid and DEX aggregator APIs via the 6551 platform.

## Overview

OpenTrade provides 7 specialized skills for comprehensive blockchain trading operations:

1. **opentrade-dex-swap** - DEX swap operations (quote, swap, approve, liquidity)
2. **opentrade-transaction** - Transaction management (gas, simulation, broadcast, tracking)
3. **opentrade-portfolio** - Wallet / Portfolio operations (balances, portfolio value, transaction history)
4. **opentrade-market** - Market data (prices, K-line, trades, smart money signals)
5. **opentrade-token** - Token information (search, info, holders, trending)
6. **opentrade-wallet** - Custodial wallet management (create wallet, get account, swap, withdraw). Supports BSC and Solana only.
7. **opentrade-newsliquid** - CEX trading via Newsliquid gateway (spot & futures orders, positions, leverage, account management, wallet agent). Server-side execution with built-in risk controls.

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

### 📈 opentrade-newsliquid

CEX (centralized exchange) trading via the Newsliquid gateway. Server-side execution with built-in risk controls — no private key management required.

**Key Features:**
- Market data: ticker, K-lines, trading pair metadata
- Account management: balance summary, spot assets
- Order management: place/edit/cancel limit, market, stop-loss, take-profit orders
- Position management: view, close, historical positions
- Leverage & margin: set leverage, margin mode, position mode
- Wallet agent: create and manage wallet agents for CEX↔chain fund transfers
- Built-in 4-layer risk engine (price deviation, position limit, rate limit, balance check)

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
