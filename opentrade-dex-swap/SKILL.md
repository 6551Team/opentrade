---
name: opentrade-dex-swap
description: "This skill should be used when the user asks to quote, buy or sell a launchpad or DEX token on BNB Chain (BSC) or Robinhood Chain with their own wallet: 'how much would 0.1 BNB buy', 'buy this token', 'sell half my position', 'buy with USDC', 'is this token tradable', 'build the swap transaction', 'submit my signed transaction', 'check the receipt', or mentions four.meme, Flap, Gang, Genius, bonding curves, graduated PancakeSwap / Uniswap pools, meme coins on BSC (报价、买入、卖出、链上成交、构建交易、签名、提交交易、查回执、meme 币、四格、Flap、Gang、曲线、毕业). It prices through NewsLiquid's trade service, prints the unsigned transactions for the user's own signer, forwards what the user signed and tracks the receipts; it never holds or asks for a private key. For centralized exchanges use opentrade-cex; for custodial wallets opentrade-wallet; for other chains or a transaction built elsewhere, opentrade-gateway. Do NOT use for general programming questions about swap code."
license: Apache-2.0
metadata:
  author: 6551
  version: "2.0.0"
  homepage: "https://www.newsliquid.com"
---

# OpenTrade DEX Swap — on-chain trades on BSC and Robinhood Chain

Price and execute on-chain orders against NewsLiquid's trade service: it tracks every launchpad token on BSC and
Robinhood Chain block by block, picks the route (the platform's curve, its MemeRouter, or a graduated pool), builds and
simulates the transactions on its own node, and relays signed transactions to block builders or the chain.

The division of labour never changes:

- **This skill** prices the order (`quote`), has the service build and simulate it for the user's wallet and prints the
  unsigned transactions as a plan (`build`), forwards what the user signed after checking it against that plan
  (`submit`), and waits for receipts (`watch`).
- **The user's own signer** — a Foundry keystore or hardware wallet through `cast`, a script around ethers / viem /
  eth-account, a KMS, whatever the user already runs — signs the plan's transactions and hands back raw signed hex.
  The key stays there. Never ask for, accept, store or echo a private key, seed phrase or keystore password; if one
  appears in the conversation, say it must be treated as compromised and go on without it.

Read [references/trade-service-api.md](references/trade-service-api.md) for the request and response fields and the
plan format before interpreting anything the script prints.

## Pre-flight

All commands are `python3 scripts/dex_trade.py <command> …`, run from this skill's directory (Python 3.9+, standard
library). Output is one JSON document on stdout. Exit code 2 means the service refused the request and the JSON is its
answer (`error`, `status`, often the full `quote`); exit code 1 is a local or network problem reported on stderr.

1. Credentials. The script sends `Authorization: Bearer <token>` from `NL_TRADE_TOKEN` (a static service token or a
   principal token). Without it, it registers its own principal with the service (a P-256 challenge signature; one-hour
   tokens cached under `~/.config/opentrade-dex-swap`, or `NL_TRADE_HOME`), which needs the `cryptography` package:
   `pip install --user cryptography`. Run `python3 scripts/dex_trade.py auth` once per session; it prints the credential
   kind and expiry, never the token. Never print, log or quote a token in the conversation.
2. Base URL: `NL_TRADE_BASE` overrides `https://trade-service.newsliquid.com/v2` when a deployment says so.
3. The signer. Before the first `build`, ask which signer the user wants to sign with and the wallet address it
   controls, unless both are already known. No RPC node, wallet library or key is needed by the skill itself.

## Skill routing

- BSC or Robinhood token quoted or traded on-chain, by address, with the user's own wallet → this skill.
- Exchange orders, positions, leverage → `opentrade-cex`.
- Custodial wallet balances or custodial swaps (6551 signs server-side) → `opentrade-wallet`, `opentrade-portfolio`.
- Token search, market data → `opentrade-token`, `opentrade-market`.
- A signed transaction for another chain, or built elsewhere → `opentrade-gateway`.

## Workflow

1. Identify the token: a 0x address on `bsc` (default) or `robinhood`. Resolve a name through
   `tokens --platform … --limit 50` (recently traded, by platform or status) or `opentrade-token`, or ask the user; never
   guess an address. `token <address>` gives symbol, decimals, platform (four.meme, flap, gang, genius, pools …),
   status (`curve` = still on the bonding curve, `graduated` = on a DEX pool) and the logo.
2. Quote before anything else:
   ```bash
   python3 scripts/dex_trade.py quote --token 0x… --side buy --amount 0.1
   python3 scripts/dex_trade.py quote --token 0x… --side sell --percent 50 --account 0x… --slippage 2
   python3 scripts/dex_trade.py quote --token 0x… --side buy --amount 20 --pay USDC
   ```
   `--amount` is in human units: the native coin (BNB / ETH) or the `--pay` stablecoin for buys, tokens for sells
   (decimals come from the service). `--percent` sells or spends a share of the wallet's balance as the service reads it
   (needs `--account`). Report `summary.expectedOut`, `minOut`, `slippageBps`, `priceImpactPercent`, `provider`/`route`,
   `platform`, `status` and any `blocked` or `error` in plain words.
3. Confirm the order in chat — token, side, amount, slippage, the wallet address, expected and minimum output — and only
   then build it:
   ```bash
   python3 scripts/dex_trade.py build --token 0x… --side buy --amount 0.1 --account 0x… --out order.json
   python3 scripts/dex_trade.py build --token 0x… --side sell --percent 50 --account 0x… --slippage 2 --out order.json
   python3 scripts/dex_trade.py build --token 0x… --side buy --amount 20 --pay USDC --account 0x… --out order.json
   python3 scripts/dex_trade.py build --token 0x… --side sell --amount 1200 --account 0x… --broadcast --out order.json
   ```
   The service builds and simulates the order for that wallet; a 409 (no route, simulation reverted) ends here with
   nothing to sign. On success the output has `summary` (price, simulation, gas) and `plan`: `plan.transactions` are
   the unsigned legacy (type 0) transactions as JSON-RPC objects, in signing order with their nonces — on BSC one tip
   transfer per block builder first (same nonce; only the builder that lands the bundle is paid), then the trade
   (`approve` when an allowance is short, then `buy` / `sell`). In a quiet market the service suggests no tip and the
   bundles carry the trade alone. `--tip-wei` and `--gas-price-wei` override the service's market figures (a tip
   below the builders' minimum is raised to it); `--broadcast` plans a plain broadcast instead of bundles. Tell the
   user what will be signed: each transaction's kind, `to`, `value` and gas, the tip, and that approve is for exactly
   the amount sold.
4. Have the user's signer sign `plan.transactions[*].tx`, in order, exactly as given — same `chainId`, `nonce`,
   `gasPrice`, `gas`, `to`, `value`, `data`, as a legacy transaction, with the wallet in `from` — and collect one raw
   signed hex string per transaction. With Foundry, from the plan's values (a keystore, `--ledger` or `--trezor`; never
   paste a key into a command the conversation records):
   ```bash
   cast mktx --legacy --chain 56 --nonce 17 --gas-price 120000000 --gas-limit 21000 --value 3000000000000000 \
     --account <keystore-name> 0x<tipTo>            # a tip: empty calldata
   cast mktx --legacy --chain 56 --nonce 18 --gas-price 120000000 --gas-limit 60000 --value 0 \
     --account <keystore-name> 0x<token> 0x095ea7b3…   # a trade step: the plan's data as raw calldata
   ```
   With a library the mapping is the same object: ethers `wallet.signTransaction({ type: 0, chainId, nonce, gasPrice,
   gasLimit: gas, to, value, data })`, viem `account.signTransaction({ type: 'legacy', … })`, eth-account
   `Account.from_key(…).sign_transaction({…})` (convert the 0x-hex quantities to numbers as the library wants). Do it
   promptly: the nonce, gas price, tip and the Router `deadline` are only right for a moment. If the user changes
   anything, or time passed, run `build` again rather than editing the plan.
5. Submit the signed transactions in the plan's order; the script decodes each one, checks it against the plan field by
   field, refuses the whole order on any difference or an expired deadline, and only then forwards it:
   ```bash
   python3 scripts/dex_trade.py submit --plan order.json --signed 0x… 0x… 0x… 0x…
   python3 scripts/dex_trade.py submit --plan order.json --signed-file signed.txt   # one per line, or a JSON list
   ```
   A bundle plan goes out as one bundle per builder (its tip, then the trade, the trade hashes as
   `revertingTxHashes`); a broadcast plan as a plain broadcast. The output lists every transaction with its hash and
   `watch`, the trade hashes to track. Never submit the same plan twice; a second submission would only fail on the nonce.
6. Watch the receipts with `python3 scripts/dex_trade.py watch --hash 0x… --timeout 10` (up to 4 hashes). `status` 1
   is success, 0 a reverted transaction (gas and any tip were spent); `pending` hashes have not landed within the wait
   — run `watch` again rather than asking for another signature; a bundle that misses its block window never lands and
   the nonce stays free, so a fresh `build` is needed if the user still wants the trade. Report the hashes, what was paid
   and what was received.

## Reading the service

- A sell comes with an `approve` step for exactly the amount sold when the allowance is short; the service never asks
  for an unlimited allowance, and neither should the user sign one.
- `summary.executable` false with `error` or `blocked`, or `build` exiting 2 with `no executable route`, means no
  route right now: the token may be unknown to the service (not a launchpad or tracked pool token), its curve may be
  paused or migrating, or liquidity is below the gate. Say which, from the message; do not retry in a loop.
- `simulation.ok` false (HTTP 409 from `build`) means approve→swap reverted on the service's node; the reason is in
  `quote.execution.simulation.calls[].error`. Typical causes: the wallet lacks the coin or gas, a tax or transfer
  restriction, a curve that just graduated. Nothing is planned for such an order.
- Gas price is `max(node, market) × 1.2`; the tip is the service's market suggestion; `signer.nonce` is the latest
  nonce on the service's node. `submit` refuses a plan whose `deadline` has passed.
- `submit --broadcast <raw>…` and `submit --bundles <file>` forward transactions the user signed outside a plan; they
  are not checked against anything. Use them only when the user built the transactions elsewhere and says so.

## Safety rules

- Only prepare the token, side, amount and wallet the user asked for; never add orders, split them, or "retry with more
  slippage" on your own. A reverted trade is reported, not repeated.
- Amounts the user gives in coins stay in coins; use `--amount-wei` only when the user gives raw units.
- Never print `NL_TRADE_TOKEN` or any credential; never ask for, accept or repeat a private key, seed phrase or
  keystore password. Hashes, addresses and raw signed transactions are fine.
- `quote` and `build` are read-only for the chain; money moves only at `submit`, after the user signed. Say which step
  you are running.
- Stablecoin buys (`--pay`) are only for the coins listed in the reference; the approve is for the exact amount.

## Chains

| chain | id | native coin | stablecoins for `--pay` | how a plan is submitted |
| --- | --- | --- | --- | --- |
| `bsc` | 56 | BNB | USDC, USD1, USDT (18 decimals) | builder bundles (BlockRazor, 48 Club) with a tip, or `--broadcast` |
| `robinhood` | 4663 | ETH | USDG (6 decimals) | broadcast |
