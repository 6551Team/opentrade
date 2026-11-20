# NewsLiquid trade service — the routes the skill uses

Base URL `https://trade-service.newsliquid.com/v2` (override with `NL_TRADE_BASE`); every chain route is
`/{chain}/{route}` with `chain` = `bsc` | `robinhood`. Every request carries `Authorization: Bearer <token>`:
either a static service token or a principal token (`nlp_…`). JSON everywhere except the SSE stream.
Amounts are decimal strings in the coin's smallest unit unless a field says otherwise. The service never holds a key: it
quotes, builds unsigned transactions, simulates, and forwards what the client signed. Neither does the skill: `build`
turns the service's unsigned steps into a plan (below) that the user's own signer signs, and `submit` forwards the result.

## Credentials: `POST /bsc/auth/challenge`, `POST /bsc/auth/token`

Chain-agnostic (served by every chain process; one token works on both chains).

1. `POST …/auth/challenge` → `{ nonce, expiresAt }` (nonce valid 2 minutes, single use, 20 pending per IP).
2. Sign the nonce's UTF-8 bytes with ECDSA P-256 / SHA-256; encode the raw `r‖s` (64 bytes) as base64url.
3. `POST …/auth/token` `{ nonce, jwk: { kty: "EC", crv: "P-256", x, y }, signature }` → `{ token: "nlp_…", expiresAt (ms), principal }`.
   Tokens live 60 minutes; the principal id is the SHA-256 fingerprint of the JWK, and the service rate-limits and can
   revoke per principal (403 `principal revoked`).

`scripts/dex_trade.py auth` does this with a P-256 key kept under `~/.config/opentrade-dex-swap/` (or `NL_TRADE_HOME`).
That key only identifies the installation to the service; it is not a wallet key. Both requests must come from the same
client address (the challenge is bound to it), so the script sends them over one kept-alive connection.

## `GET /{chain}/quote`

| parameter | meaning |
| --- | --- |
| `token` | token address (required) |
| `side` | `buy` (pay the native coin or a stablecoin, receive the token) or `sell` |
| `amountIn` | smallest units of what is paid: wei of BNB/ETH or of the `input` stablecoin for buys, token units for sells |
| `percent` | instead of `amountIn`: `0–100` with up to two decimals, resolved from `account`'s balance on the service's node (sell: token balance × share; buy: native balance minus a 3M-gas reserve × share); the response echoes the resolved `amountIn` and `percent`; a zero balance is 409 |
| `account` | the wallet that signs and holds the coins; with it the quote also contains the executable plan (`execution.tx`, `approveTx`); without it only the price |
| `recipient` | who receives the output (default: `account`) |
| `slippage` / `slippageBps` | percent (`0.5`) or basis points; default 1%, max 50%; `minOut = expectedOut × (1 − slippage)` is written into the transaction |
| `deadlineSec` | Router deadline, 30–3600 (default 120) |
| `input` | stablecoin address for a buy paid in that coin (BSC USDC `0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d`, USD1 `0x8d0d000ee44948fc98c9b98a4fa4921476f08b0d`, USDT `0x55d398326f99059ff775485246999027b3197955`, all 18 decimals; Robinhood USDG `0x5fc5360d0400a0fd4f2af552add042d716f1d168`, 6 decimals) |
| `payout` | sells: `native` (default, BNB/ETH) or `quote` (the platform's quote coin) |
| `tx=0` | skip building the transaction even with `account` |

Response (`QuoteResponse`):

- `token`, `platform` (`fourmeme`, `flap`, `gang`, `genius`, `openfour`, `pools`, … or `unknown`), `status` (`curve` | `graduated` | `unknown`), `quoteToken`, `quoteDecimals`, `side`, `amountIn`, `amountInToken` (`0x000…0` = the native coin), `block`, `ageMs`, `head`.
- `indicative`: `{ amountOut, amountOutToken, amountOutNative?, spent?, fees, source, block, blocked?, route? }` — the price from the curve formula or the pool; `blocked` names why nothing executable exists.
- `execution` (with `account`): `{ provider: "router" | "direct" | "okx", amountOut, minOut, slippageBps, slippageProtected, route[], feePercent?, priceImpactPercent?, tx, approveTx?, approval: { token, spender, allowance, sufficient }, payoutToken, deadline, note?, error?, simulation?, signer? }`.
- `submission`: `{ mode: "bundle" | "rpc", providers: [{ id, name, tipTo, endpoints?, method?, headers? }], tip: { suggestedWei, minWei }, maxBlocks }`.
- `consistency`: local vs quoter comparison (`diffBps`, `suspect`).

## `GET /{chain}/trade`

Same parameters; `account` is required. Builds, then simulates approve → swap sequentially on the service's node.

Success (200):

```json
{
  "token": "0x…", "platform": "fourmeme", "status": "curve", "head": "70123456", "side": "buy",
  "amountIn": "100000000000000000", "amountInToken": "0x0000000000000000000000000000000000000000",
  "quoteToken": "0x…", "quoteDecimals": 18,
  "expectedOut": "123456789000000000000000", "expectedOutToken": "0x<token>", "minOut": "122222…",
  "slippageBps": 100, "slippagePercent": "1", "slippageProtected": true, "payoutToken": "0x0…0",
  "provider": "router", "deadline": "1763600000", "note": null,
  "steps": [
    { "kind": "approve", "description": "approve 0x<spender> for …", "tx": { "chainId": 56, "from": "0x<account>", "to": "0x<token>", "data": "0x095ea7b3…", "value": "0", "gas": "60000", "gasSource": "simulate" } },
    { "kind": "buy", "description": "buy via router (…)", "tx": { "chainId": 56, "from": "0x<account>", "to": "0x<router>", "data": "0x…", "value": "100000000000000000", "gas": "420000", "gasSource": "simulate" } }
  ],
  "submission": { "mode": "bundle", "providers": [{ "id": "blockrazor", "name": "BlockRazor", "tipTo": "0x…" }, { "id": "48club", "name": "48 Club", "tipTo": "0x…" }], "tip": { "suggestedWei": "…", "minWei": "…" }, "maxBlocks": 10 },
  "signer": { "address": "0x<account>", "nonce": 17, "balance": "…", "gasPrice": "100000000" },
  "simulation": { "ok": true, "block": "70123456", "ms": 41, "calls": [{ "kind": "approve", "status": "success", "gasUsed": "46000" }, { "kind": "buy", "status": "success", "gasUsed": "280000" }] },
  "tip": { "suggestedWei": "…", "floorWei": "…", "p50Wei": "…", "p90Wei": "…", "basis": "market", "gasPriceWei": "…", "gasBasis": "node" },
  "serverMs": 63,
  "quote": { "…the QuoteResponse…" }
}
```

- `steps` are signed in order with consecutive nonces: a sell is `[approve?, sell]`; a stablecoin buy `[approve, buy]`; a native buy `[buy]`. The approve is for exactly `amountIn`.
- `gas` already carries headroom (max(pre-refund use × 1.5, use + 250k), capped at 3M). `gasSource`: `simulate` | `estimate` | `hint` | `okx`.
- `signer.nonce` is the latest nonce on the service's node (not pending). `signer.gasPrice` is the node's reading; `tip.gasPriceWei` the market's. The plan prices the transactions at `max(signer.gasPrice, tip.gasPriceWei) × 1.2`, as the NewsLiquid wallet does.
- `deadline` is the Router's on-chain expiry (unix seconds).

Refusals: 400 for bad parameters (`error` text); 409 `{ error, quote }` when there is no executable route or the simulation reverted (`quote.execution.simulation.calls[].error` has the revert reason); 429 rate-limited; 401 bad or expired credential; 403 revoked principal.

## The plan: what `build` prints and `submit` checks

`scripts/dex_trade.py build --account 0x… --out order.json` turns the `/trade` answer into the transactions the user's own
signer signs, in order, as JSON-RPC transaction objects (the shape of `eth_signTransaction`, every quantity 0x-hex):

```json
{
  "chain": "bsc", "chainId": 56, "account": "0x<account>", "label": "sell 12.5 TEST", "mode": "bundle",
  "nonce": 17, "gasPriceWei": "120000000", "tipWei": "3000000000000000",
  "providers": [{ "id": "blockrazor", "name": "BlockRazor", "tipTo": "0x…" }, { "id": "48club", "name": "48 Club", "tipTo": "0x…" }],
  "maxBlocks": 10, "head": "70123456", "deadline": "1763600000", "builtAt": 1763599900000,
  "transactions": [
    { "index": 0, "kind": "tip", "provider": "blockrazor", "tx": { "type": "0x0", "chainId": "0x38", "nonce": "0x11", "gasPrice": "0x7270e00", "gas": "0x5208", "to": "0x<tipTo>", "value": "0xaa87bee538000", "data": "0x", "from": "0x<account>" } },
    { "index": 1, "kind": "tip", "provider": "48club",  "tx": { "…": "the same at the same nonce, to the other builder" } },
    { "index": 2, "kind": "approve", "description": "approve …", "tx": { "type": "0x0", "chainId": "0x38", "nonce": "0x12", "gasPrice": "0x7270e00", "gas": "0xea60", "to": "0x<token>", "value": "0x0", "data": "0x095ea7b3…", "from": "0x<account>" } },
    { "index": 3, "kind": "sell", "description": "sell via router", "tx": { "type": "0x0", "chainId": "0x38", "nonce": "0x13", "…": "…" } }
  ]
}
```

- Every transaction is a legacy (type 0, EIP-155) transaction. The signer signs each `tx` exactly as given — same
  `chainId`, `nonce`, `gasPrice`, `gas`, `to`, `value`, `data` — and returns the raw signed hex. `from` is the account the
  wallet must sign with. The ordering is the nonce order: a bundle plan with a tip has one tip transfer per builder at
  nonce `n` (only the builder that includes its bundle gets paid; the other tips die with the nonce), then the trade at
  `n+1…`; a bundle plan with a zero tip (`tipWei: "0"`, what the service suggests in a quiet market) and a broadcast
  plan (`mode: "rpc"`: Robinhood Chain or `--broadcast`) have only the trade, from `n`. A `--tip-wei` below
  `tipMinWei` is raised to it.
- `submit --plan order.json --signed <raw>…` decodes each signed transaction, checks it field by field against the plan's
  transaction at that position (chain id from `v`, nonce, gas price, gas, to, value, data) and refuses the whole
  submission on any difference or when `deadline` has passed. Transaction hashes are keccak-256 of the signed bytes.
- A bundle plan is sent as `{ "bundles": [{ provider, txs: [tip, …trade], revertingTxHashes: […trade hashes] }, …] }`,
  one bundle per builder; a broadcast plan as `{ "rpc": { "txs": […trade] } }`.
- The plan is right only for a moment: `signer.nonce` moves with any other transaction of the wallet, the gas price and
  tip follow the market, and the Router rejects the swap after `deadline`. Sign and submit promptly; build again instead
  of editing a plan.

## `POST /{chain}/submit`

Forwards signed transactions; the service never signs.

- Broadcast: `{ "rpc": { "txs": ["0x…", …] } }` (1–16 raw transactions, sent in order a few ms apart) → `{ "rpc": { "hashes": [...], "error"? }, "hashes": [...], "serverMs" }`. Available on both chains.
- Bundles (BSC): `{ "bundles": [{ "provider": "blockrazor", "txs": ["0x<tip>", "0x<approve>", "0x<swap>"], "revertingTxHashes": ["0x<approve hash>", "0x<swap hash>"], "maxBlockNumber"? }] }` → `{ "head", "maxBlockNumber", "results": [{ "provider", "bundleHash" | "error", "pending"? }], "hashes": [...] }`.
  One bundle per provider, each with its own tip transfer to that provider's `tipTo` at the same nonce `n`, then the trade transactions at `n+1…`; whichever builder includes its bundle gets the tip, the other bundles die with the nonce. `revertingTxHashes` lists the trade transactions so a reverted swap lands as a failed transaction instead of the whole bundle being dropped (a bundle without it is dropped whole when any transaction reverts). `maxBlockNumber` defaults to the service's head + `submission.maxBlocks`.
- Every transaction in a submission is tracked for receipts right away.

## `GET /{chain}/watch?hash=0x…[&hash=…]&timeoutMs=5000&statusMs=100` (SSE)

Up to 4 hashes; events in order: `open`, `status` (`{ pending, elapsedMs }`, every `statusMs`), `receipt` (`{ hash, status: 1|0, blockNumber, gasUsed, elapsedMs }`, as soon as known), `timeout` per hash still pending at `timeoutMs` (0.5–15 s), `end`. A hash that timed out can be watched again later; a bundle lands within its block window or never.

## Facts

- `GET /{chain}/meta?token=` → `{ address, chainId, symbol, name, decimals, platform, origin, status, logo, logoSource }` — the token's facts (works for non-launchpad tokens too).
- `GET /{chain}/token/{address}` → the registry record (curve state, pools, quote token) or 404 `unknown token`.
- `GET /{chain}/tokens?platform=&status=&limit=` → recently traded tokens, newest trades first (limit 1–1000).
- `GET /{chain}/head` → `{ head, primary, network, source, lag, lagging, rpc }`.
- `GET /{chain}/health` → readiness, ingest window, ladder/trench/receipt stats.
- `GET /bsc/prices?symbols=BNB,ETH` → `{ at, source: "binance", stale, prices: { BNB: { usd, at }, … } }` (major coins only; meme tokens are priced through `/quote`).

## Chains

| chain | chainId | native | explorer |
| --- | --- | --- | --- |
| bsc | 56 | BNB (18) | bscscan.com |
| robinhood | 4663 | ETH (18) | robin.etherscan.io |
