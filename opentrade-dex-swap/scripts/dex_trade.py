#!/usr/bin/env python3
"""On-chain trade CLI for launchpad and DEX tokens on BNB Chain and Robinhood Chain, against the NewsLiquid trade service.

The skill never holds a wallet key. `quote` prices an order; `build` has the service build and simulate it for the user's
wallet and prints a plan: the unsigned transactions, as JSON-RPC objects, in the order the user's own signer signs them;
`submit` checks the signed transactions against that plan and forwards them (builder bundles or a plain broadcast);
`watch` waits for the receipts. Standard library only, except that registering the skill's own service principal (when no
NL_TRADE_TOKEN is given) needs `cryptography`.
Prints one JSON document to stdout; diagnostics go to stderr.
Exit codes: 0 ok, 1 usage / network / local error, 2 the service refused the request (HTTP 4xx/5xx; its body is the JSON printed).
Credentials are read from the environment or the skill's home and never printed.
"""
from __future__ import annotations

import argparse
import base64
import http.client as httpclient
import json
import os
import re
import sys
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

VERSION = "2.0.0"
USER_AGENT = f"OpenTrade-DexSwap/{VERSION}"
DEFAULT_BASE = "https://trade-service.newsliquid.com/v2"
ZERO = "0x" + "0" * 40
CHAINS = {
    "bsc": {"id": 56, "native": "BNB", "decimals": 18},
    "robinhood": {"id": 4663, "native": "ETH", "decimals": 18},
}
# Stablecoins the service accepts as the paying coin of a buy (`input=`), with their decimals.
STABLES = {
    "bsc": {
        "USDC": ("0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d", 18),
        "USD1": ("0x8d0d000ee44948fc98c9b98a4fa4921476f08b0d", 18),
        "USDT": ("0x55d398326f99059ff775485246999027b3197955", 18),
    },
    "robinhood": {"USDG": ("0x5fc5360d0400a0fd4f2af552add042d716f1d168", 6)},
}
ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
TX_HASH = re.compile(r"^0x[0-9a-fA-F]{64}$")
HEX = re.compile(r"^0x(?:[0-9a-fA-F]{2})*$")
TIMEOUT = 20
TOKEN_REFRESH_MARGIN_S = 120
TIP_GAS = 21_000  # a plain transfer: the builder tip
GAS_PRICE_MARGIN = (12, 10)  # a fifth over the node's or the market's reading, as the NewsLiquid wallet does
MAX_BROADCAST = 16


class ApiError(Exception):
    """The service answered with an error (status ≥ 400), or the request never got an answer (status 0)."""

    def __init__(self, status: int, payload: dict, url: str = "") -> None:
        super().__init__(payload.get("error") or f"HTTP {status}")
        self.status = status
        self.payload = payload
        self.url = url


# ----------------------------------------------------------------------------- configuration and credentials
def home_dir() -> Path:
    return Path(os.environ.get("NL_TRADE_HOME") or Path.home() / ".config" / "opentrade-dex-swap")


def base_url() -> str:
    return (os.environ.get("NL_TRADE_BASE") or DEFAULT_BASE).rstrip("/")


def chain_url(chain: str, route: str) -> str:
    return f"{base_url()}/{chain}/{route}"


def auth_url(route: str) -> str:
    # The auth routes are chain-agnostic: every chain's process serves them and one token works on both chains.
    return f"{base_url()}/bsc/auth/{route}"


def b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def cached_token() -> dict | None:
    path = home_dir() / "token.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("token"), str):
        return None
    expires = data.get("expiresAt")
    if not isinstance(expires, (int, float)) or expires / 1000 - TOKEN_REFRESH_MARGIN_S <= time.time():
        return None
    return data


def principal_private_key():
    """The skill's own P-256 key for the service's principal login, created on first use (needs `cryptography`).
    It identifies this installation to the service; it is not a wallet key and cannot move funds."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    path = home_dir() / "principal.pem"
    if path.exists():
        return serialization.load_pem_private_key(path.read_bytes(), password=None)
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    write_private(path, pem.decode("ascii"))
    return key


def principal_jwk(key) -> dict:
    numbers = key.public_key().public_numbers()
    return {"kty": "EC", "crv": "P-256", "x": b64u(numbers.x.to_bytes(32, "big")), "y": b64u(numbers.y.to_bytes(32, "big"))}


def sign_challenge(key, nonce: str) -> str:
    """ECDSA P-256 / SHA-256 over the nonce's UTF-8 bytes, as raw r‖s (64 bytes) in base64url — what the service verifies."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    r, s = decode_dss_signature(key.sign(nonce.encode("utf-8"), ec.ECDSA(hashes.SHA256())))
    return b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))


class Session:
    """One kept-alive HTTPS connection: the challenge and the token request must reach the service from the same client address
    (the challenge is bound to it), which separate connections through a NAT pool or a proxy do not guarantee."""

    def __init__(self, url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname:
            raise ApiError(0, {"error": f"the service URL must be https: {url}"})
        self.connection = httpclient.HTTPSConnection(parts.hostname, parts.port or 443, timeout=TIMEOUT)

    def post(self, url: str, body: dict) -> dict:
        parts = urlsplit(url)
        path = parts.path + (f"?{parts.query}" if parts.query else "")
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json", "Content-Type": "application/json"}
        try:
            self.connection.request("POST", path, body=json.dumps(body).encode("utf-8"), headers=headers)
            response = self.connection.getresponse()
            raw = response.read()
        except (httpclient.HTTPException, OSError) as e:
            raise ApiError(0, {"error": f"network: {e}"}, url) from None
        try:
            payload = json.loads(raw) if raw else {}
        except ValueError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {"body": payload}
        if response.status >= 400:
            payload.setdefault("error", f"HTTP {response.status}")
            raise ApiError(response.status, payload, url)
        return payload

    def close(self) -> None:
        try:
            self.connection.close()
        except OSError:
            pass


open_session = Session  # replaced in tests


def obtain_principal_token() -> dict:
    """Challenge → signature → one-hour token, cached under the skill's home."""
    try:
        import cryptography  # noqa: F401
    except ImportError as e:
        raise ApiError(0, {"error": "no credentials: set NL_TRADE_TOKEN, or `pip install --user cryptography` so the skill can register its own principal"}) from e
    session = open_session(auth_url("challenge"))
    try:
        challenge = session.post(auth_url("challenge"), {})
        nonce = challenge.get("nonce") if isinstance(challenge, dict) else None
        if not isinstance(nonce, str) or not nonce:
            raise ApiError(0, {"error": "challenge response has no nonce"})
        key = principal_private_key()
        issued = session.post(auth_url("token"), {"nonce": nonce, "jwk": principal_jwk(key), "signature": sign_challenge(key, nonce)})
    finally:
        session.close()
    if not isinstance(issued, dict) or not isinstance(issued.get("token"), str) or not isinstance(issued.get("expiresAt"), (int, float)):
        raise ApiError(0, {"error": "token response invalid"})
    record = {"token": issued["token"], "expiresAt": issued["expiresAt"], "principal": issued.get("principal")}
    write_private(home_dir() / "token.json", json.dumps(record))
    return record


def credentials(explicit: str | None = None) -> str:
    """The bearer token: an explicit or NL_TRADE_TOKEN value (static or principal token), else the cached / freshly obtained principal token."""
    given = (explicit or os.environ.get("NL_TRADE_TOKEN") or "").strip()
    if given:
        return given
    cached = cached_token()
    if cached:
        return cached["token"]
    return obtain_principal_token()["token"]


# ----------------------------------------------------------------------------- HTTP
def http(method: str, url: str, *, token: str | None = None, body: dict | None = None, timeout: float = TIMEOUT, stream: bool = False):
    headers = {"User-Agent": USER_AGENT, "Accept": "text/event-stream" if stream else "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers, method=method)
    try:
        response = urlopen(request, timeout=timeout)
    except HTTPError as e:
        raw = e.read() if hasattr(e, "read") else b""
        close = getattr(e, "close", None)
        if callable(close):
            close()
        try:
            payload = json.loads(raw) if raw else {}
        except ValueError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {"body": payload}
        payload.setdefault("error", f"HTTP {e.code}")
        raise ApiError(e.code, payload, url) from None
    except URLError as e:
        raise ApiError(0, {"error": f"network: {getattr(e, 'reason', e)}"}, url) from None
    if stream:
        return response
    raw = response.read()
    close = getattr(response, "close", None)
    if callable(close):
        close()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except ValueError:
        raise ApiError(0, {"error": "the service did not answer with JSON"}, url) from None


def get(chain: str, route: str, params: dict | list | None, token: str, timeout: float = TIMEOUT):
    url = chain_url(chain, route)
    if params:
        url += "?" + urlencode(params)
    return http("GET", url, token=token, timeout=timeout)


def post(chain: str, route: str, body: dict, token: str, timeout: float = TIMEOUT):
    return http("POST", chain_url(chain, route), token=token, body=body, timeout=timeout)


# ----------------------------------------------------------------------------- amounts and token facts
def to_units(amount: str, decimals: int) -> int:
    try:
        value = Decimal(str(amount))
    except InvalidOperation:
        raise ValueError(f"amount is not a number: {amount}") from None
    if not value.is_finite() or value <= 0:
        raise ValueError("amount must be a positive number")
    scaled = value.scaleb(decimals)
    if scaled != scaled.to_integral_value():
        raise ValueError(f"amount has more than {decimals} decimals")
    return int(scaled)


def from_units(raw, decimals: int) -> str:
    try:
        value = Decimal(int(raw)).scaleb(-decimals)
    except (ValueError, TypeError, InvalidOperation):
        return str(raw)
    text = f"{value:f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def as_int(value) -> int:
    """An integer from the service's decimal strings, a plan's JSON-RPC hex quantities, or a plain int."""
    if isinstance(value, bool):
        raise ValueError(f"not a quantity: {value!r}")
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if text.lower().startswith("0x"):
        return int(text, 16)
    if not re.fullmatch(r"-?\d+", text):
        raise ValueError(f"not a quantity: {value!r}")
    return int(text)


def quantity(value) -> str:
    """A JSON-RPC quantity: 0x-prefixed hex without leading zeros (`0x0` for zero)."""
    n = as_int(value)
    if n < 0:
        raise ValueError("quantities are not negative")
    return hex(n)


_meta_cache: dict[tuple[str, str], dict] = {}


def token_meta(chain: str, address: str, token: str) -> dict:
    key = (chain, address.lower())
    if key not in _meta_cache:
        meta = get(chain, "meta", {"token": address}, token)
        _meta_cache[key] = meta if isinstance(meta, dict) else {}
    return _meta_cache[key]


def token_decimals(chain: str, address: str, token: str) -> int:
    decimals = token_meta(chain, address, token).get("decimals")
    if not isinstance(decimals, int) or not 0 <= decimals <= 36:
        raise ValueError(f"the service does not know the decimals of {address}; pass --amount-wei")
    return decimals


def output_decimals(chain: str, token_address: str, out_token: str, token: str, quote_decimals=None) -> int | None:
    if out_token.lower() == ZERO:
        return CHAINS[chain]["decimals"]
    if out_token.lower() == token_address.lower():
        try:
            return token_decimals(chain, token_address, token)
        except (ValueError, ApiError):
            return None
    for _symbol, (address, decimals) in STABLES[chain].items():
        if address == out_token.lower():
            return decimals
    return quote_decimals if isinstance(quote_decimals, int) else None


# ----------------------------------------------------------------------------- request parameters
def percent_text(value: str) -> str:
    try:
        pct = Decimal(str(value))
    except InvalidOperation:
        raise ValueError(f"percent is not a number: {value}") from None
    if not pct.is_finite() or pct <= 0 or pct > 100:
        raise ValueError("percent must be within (0, 100]")
    if pct != pct.quantize(Decimal("0.01")):
        raise ValueError("percent takes at most two decimals")
    return f"{pct.normalize():f}"


def slippage_text(value: str) -> str:
    try:
        pct = Decimal(str(value))
    except InvalidOperation:
        raise ValueError(f"slippage is not a number: {value}") from None
    if not pct.is_finite() or pct < 0 or pct > 50:
        raise ValueError("slippage is a percentage within [0, 50]")
    return f"{pct.normalize():f}"


def trade_params(args, chain: str, token: str) -> tuple[dict, dict]:
    """Query parameters for /quote and /trade from the CLI arguments; also what the amount was counted in, for the summary."""
    if not ADDRESS.match(args.token):
        raise ValueError("--token must be a 0x address")
    if args.side not in ("buy", "sell"):
        raise ValueError("--side must be buy or sell")
    params: dict = {"token": args.token, "side": args.side}
    pay = {"token": ZERO, "symbol": CHAINS[chain]["native"], "decimals": CHAINS[chain]["decimals"]}
    if getattr(args, "pay", None):
        symbol = args.pay.upper()
        if args.side != "buy":
            raise ValueError("--pay applies to buys only (sells take --payout)")
        stable = STABLES[chain].get(symbol)
        if not stable:
            raise ValueError(f"--pay must be one of {', '.join(STABLES[chain])} on {chain}")
        params["input"] = stable[0]
        pay = {"token": stable[0], "symbol": symbol, "decimals": stable[1]}
    if args.side == "sell":
        pay = {"token": args.token, "symbol": None, "decimals": None}
    if getattr(args, "percent", None) is not None:
        if not args.account:
            raise ValueError("--percent needs --account (the service reads that wallet's balance)")
        params["percent"] = percent_text(args.percent)
    elif getattr(args, "amount_wei", None):
        if not re.fullmatch(r"\d+", str(args.amount_wei)) or int(args.amount_wei) <= 0:
            raise ValueError("--amount-wei must be a positive integer")
        params["amountIn"] = str(int(args.amount_wei))
    elif getattr(args, "amount", None):
        decimals = pay["decimals"] if pay["decimals"] is not None else token_decimals(chain, args.token, token)
        params["amountIn"] = str(to_units(args.amount, decimals))
    else:
        raise ValueError("one of --amount, --amount-wei or --percent is required")
    if args.account:
        if not ADDRESS.match(args.account):
            raise ValueError("--account must be a 0x address")
        params["account"] = args.account
    if getattr(args, "recipient", None):
        if not ADDRESS.match(args.recipient):
            raise ValueError("--recipient must be a 0x address")
        params["recipient"] = args.recipient
    if getattr(args, "slippage", None) is not None:
        params["slippage"] = slippage_text(args.slippage)
    if getattr(args, "deadline", None):
        if not 30 <= int(args.deadline) <= 3600:
            raise ValueError("--deadline is in seconds, 30–3600")
        params["deadlineSec"] = str(int(args.deadline))
    if getattr(args, "payout", None):
        if args.side != "sell":
            raise ValueError("--payout applies to sells only")
        params["payout"] = args.payout
    return params, pay


# ----------------------------------------------------------------------------- summaries
def amount_view(raw, decimals: int | None, token_address: str | None, symbol: str | None = None) -> dict:
    view: dict = {"raw": str(raw) if raw is not None else None}
    if raw is not None and decimals is not None:
        view["human"] = from_units(raw, decimals)
    if token_address:
        view["token"] = token_address
    if symbol:
        view["symbol"] = symbol
    return view


def summarize_quote(chain: str, q: dict, token: str, pay: dict) -> dict:
    execution = q.get("execution") or {}
    indicative = q.get("indicative") or {}
    side = q.get("side")
    token_address = q.get("token") or ""
    meta = token_meta(chain, token_address, token) if ADDRESS.match(token_address) else {}
    if side == "buy":
        out_token = token_address
    else:
        out_token = execution.get("payoutToken") or indicative.get("amountOutToken") or ZERO
    amount_out = execution.get("amountOut") or indicative.get("amountOut")
    out_decimals = output_decimals(chain, token_address, out_token, token, q.get("quoteDecimals"))
    in_decimals = pay["decimals"] if pay["decimals"] is not None else meta.get("decimals")
    submission = q.get("submission") or {}
    summary = {
        "chain": chain,
        "token": token_address,
        "symbol": meta.get("symbol"),
        "platform": q.get("platform"),
        "status": q.get("status"),
        "side": side,
        "amountIn": amount_view(q.get("amountIn"), in_decimals if isinstance(in_decimals, int) else None, q.get("amountInToken"), pay["symbol"] if side == "buy" else meta.get("symbol")),
        "expectedOut": amount_view(amount_out, out_decimals, out_token, meta.get("symbol") if side == "buy" else (CHAINS[chain]["native"] if out_token.lower() == ZERO else None)),
        "minOut": amount_view(execution.get("minOut"), out_decimals, out_token) if execution.get("minOut") is not None else None,
        "slippageBps": execution.get("slippageBps"),
        "slippageProtected": execution.get("slippageProtected"),
        "provider": execution.get("provider"),
        "route": execution.get("route"),
        "priceImpactPercent": execution.get("priceImpactPercent"),
        "feePercent": execution.get("feePercent"),
        "executable": bool(execution.get("tx")) and not execution.get("error"),
        "error": execution.get("error"),
        "blocked": indicative.get("blocked"),
        "quoteSource": indicative.get("source"),
        "block": q.get("block"),
        "head": q.get("head"),
        "submission": {"mode": submission.get("mode"), "providers": [p.get("id") for p in submission.get("providers") or []], "tip": submission.get("tip"), "maxBlocks": submission.get("maxBlocks")} if submission else None,
    }
    if q.get("percent") is not None:
        summary["percent"] = q.get("percent")
    return summary


def summarize_trade(chain: str, t: dict, token: str, pay: dict) -> dict:
    quote = t.get("quote") or {}
    base = summarize_quote(chain, {**quote, "submission": t.get("submission"), "head": t.get("head")}, token, pay) if quote else {"chain": chain, "token": t.get("token"), "side": t.get("side")}
    out_token = t.get("expectedOutToken") or ZERO
    out_decimals = output_decimals(chain, t.get("token") or "", out_token, token, t.get("quoteDecimals"))
    base.update(
        {
            "platform": t.get("platform"),
            "status": t.get("status"),
            "expectedOut": amount_view(t.get("expectedOut"), out_decimals, out_token, base.get("expectedOut", {}).get("symbol") if isinstance(base.get("expectedOut"), dict) else None),
            "minOut": amount_view(t.get("minOut"), out_decimals, out_token) if t.get("minOut") is not None else None,
            "slippageBps": t.get("slippageBps"),
            "slippagePercent": t.get("slippagePercent"),
            "slippageProtected": t.get("slippageProtected"),
            "provider": t.get("provider"),
            "deadline": t.get("deadline"),
            "note": t.get("note"),
            "executable": bool(t.get("steps")),
            "steps": [{"kind": s.get("kind"), "description": s.get("description"), "to": (s.get("tx") or {}).get("to"), "value": (s.get("tx") or {}).get("value"), "gas": (s.get("tx") or {}).get("gas"), "gasSource": (s.get("tx") or {}).get("gasSource")} for s in t.get("steps") or []],
            "simulation": {k: v for k, v in (t.get("simulation") or {}).items() if k in ("ok", "block", "calls")} or None,
            "signer": t.get("signer"),
            "tip": t.get("tip"),
            "serverMs": t.get("serverMs"),
        }
    )
    if t.get("percent") is not None:
        base["percent"] = t.get("percent")
    return base


def short_address(address: str) -> str:
    return f"{address[:6]}…{address[-4:]}" if ADDRESS.match(address or "") else str(address)


def order_label(side: str, symbol: str, amount_in: dict | None, percent: str | None) -> str:
    if side == "buy":
        paid = f"{amount_in.get('human') or amount_in.get('raw')} {amount_in.get('symbol') or ''}".strip() if amount_in else ""
        return f"buy {symbol}" + (f" with {paid}" if paid else "")
    if percent is not None:
        return f"sell {percent}% {symbol}"
    sold = amount_in.get("human") if amount_in else None
    return f"sell {sold} {symbol}" if sold else f"sell {symbol}"


# ----------------------------------------------------------------------------- keccak-256 and RLP (to check signed transactions)
KECCAK_RC = (
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000, 0x000000000000808B, 0x0000000080000001,
    0x8000000080008081, 0x8000000000008009, 0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003, 0x8000000000008002, 0x8000000000000080,
    0x000000000000800A, 0x800000008000000A, 0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
)
KECCAK_ROT = ((0, 36, 3, 41, 18), (1, 44, 10, 45, 2), (62, 6, 43, 15, 61), (28, 55, 25, 21, 56), (27, 20, 39, 8, 14))
MASK64 = (1 << 64) - 1


def _rol(x: int, n: int) -> int:
    return ((x << n) | (x >> (64 - n))) & MASK64 if n else x


def _keccak_f1600(a: list) -> list:
    for rc in KECCAK_RC:
        c = [a[x][0] ^ a[x][1] ^ a[x][2] ^ a[x][3] ^ a[x][4] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rol(c[(x + 1) % 5], 1) for x in range(5)]
        a = [[a[x][y] ^ d[x] for y in range(5)] for x in range(5)]
        b = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                b[y][(2 * x + 3 * y) % 5] = _rol(a[x][y], KECCAK_ROT[x][y])
        a = [[b[x][y] ^ ((~b[(x + 1) % 5][y]) & b[(x + 2) % 5][y]) for y in range(5)] for x in range(5)]
        a[0][0] ^= rc
    return a


def keccak256(data: bytes) -> bytes:
    """Keccak-256 (the Ethereum hash; not SHA3-256), pure Python: a transaction hash is keccak256 of its signed encoding."""
    rate = 136
    padded = bytearray(data) + b"\x01"
    while len(padded) % rate:
        padded.append(0)
    padded[-1] |= 0x80
    a = [[0] * 5 for _ in range(5)]
    for offset in range(0, len(padded), rate):
        for i in range(rate // 8):
            a[i % 5][i // 5] ^= int.from_bytes(padded[offset + 8 * i : offset + 8 * i + 8], "little")
        a = _keccak_f1600(a)
    return b"".join(a[i % 5][i // 5].to_bytes(8, "little") for i in range(4))


def _rlp_item(data: bytes, pos: int):
    if pos >= len(data):
        raise ValueError("RLP: truncated")
    first = data[pos]
    if first < 0x80:
        return data[pos : pos + 1], pos + 1
    if first < 0xB8:
        length, start = first - 0x80, pos + 1
    elif first < 0xC0:
        size = first - 0xB7
        length, start = int.from_bytes(data[pos + 1 : pos + 1 + size], "big"), pos + 1 + size
    elif first < 0xF8:
        length, start = first - 0xC0, pos + 1
    else:
        size = first - 0xF7
        length, start = int.from_bytes(data[pos + 1 : pos + 1 + size], "big"), pos + 1 + size
    end = start + length
    if end > len(data):
        raise ValueError("RLP: truncated")
    if first < 0xC0:
        return bytes(data[start:end]), end
    items, cursor = [], start
    while cursor < end:
        item, cursor = _rlp_item(data, cursor)
        items.append(item)
    if cursor != end:
        raise ValueError("RLP: malformed list")
    return items, end


def rlp_decode(data: bytes):
    item, end = _rlp_item(data, 0)
    if end != len(data):
        raise ValueError("RLP: trailing bytes")
    return item


def decode_signed(raw: str) -> dict:
    """The fields of a signed legacy (type 0) transaction and its hash, from its raw hex."""
    if not isinstance(raw, str) or not HEX.match(raw) or len(raw) <= 2:
        raise ValueError("a signed transaction is 0x-prefixed hex")
    data = bytes.fromhex(raw[2:])
    if data[0] < 0xC0:
        kind = f"type {data[0]}" if data[0] < 0x80 else "not a transaction"
        raise ValueError(f"{kind}: sign the plan's transactions as legacy (type 0) transactions")
    fields = rlp_decode(data)
    if not isinstance(fields, list) or len(fields) != 9 or not all(isinstance(f, bytes) for f in fields):
        raise ValueError("not a signed legacy transaction (expected 9 RLP fields)")
    nonce, gas_price, gas, to, value, payload, v, _r, _s = fields
    v_int = int.from_bytes(v, "big")
    return {
        "nonce": int.from_bytes(nonce, "big"),
        "gasPrice": int.from_bytes(gas_price, "big"),
        "gas": int.from_bytes(gas, "big"),
        "to": "0x" + to.hex() if to else None,
        "value": int.from_bytes(value, "big"),
        "data": "0x" + payload.hex(),
        "chainId": (v_int - 35) // 2 if v_int >= 35 else None,
        "hash": "0x" + keccak256(data).hex(),
    }


# ----------------------------------------------------------------------------- the plan: unsigned transactions for the user's signer
def gas_price_for(args, trade: dict) -> int:
    if getattr(args, "gas_price_wei", None):
        return as_int(args.gas_price_wei)
    signer_state = trade.get("signer") or {}
    advice = trade.get("tip") or {}
    quoted = max(as_int(signer_state.get("gasPrice") or 0), as_int(advice.get("gasPriceWei") or 0))
    if quoted <= 0:
        raise ValueError("the service gave no gas price; pass --gas-price-wei")
    return quoted * GAS_PRICE_MARGIN[0] // GAS_PRICE_MARGIN[1]


def tip_for(args, trade: dict) -> tuple[int, int]:
    """The builder tip in wei and the builders' minimum: --tip-wei, else the service's market suggestion. A positive tip
    below the minimum is raised to it (as the NewsLiquid wallet does); zero means bundles without a tip transfer."""
    submission = trade.get("submission") or {}
    if getattr(args, "broadcast", False) or submission.get("mode") != "bundle":
        return 0, 0
    advice = trade.get("tip") or {}
    floor = submission.get("tip") or {}
    minimum = as_int(floor.get("minWei") or 0)
    tip = as_int(args.tip_wei) if getattr(args, "tip_wei", None) is not None else as_int(advice.get("suggestedWei") or floor.get("suggestedWei") or 0)
    if tip < 0:
        raise ValueError("--tip-wei is not negative")
    if 0 < tip < minimum:
        tip = minimum
    return tip, minimum


def unsigned(tx: dict, nonce: int, gas_price: int, chain_id: int, account: str) -> dict:
    """A legacy (type 0) transaction as a JSON-RPC object (the shape of eth_signTransaction), from the service's unsigned step."""
    for field in ("to", "gas"):
        if tx.get(field) in (None, ""):
            raise ValueError(f"unsigned step lacks {field}")
    if not ADDRESS.match(str(tx["to"])):
        raise ValueError(f"unsigned step has a bad address: {tx['to']}")
    return {
        "type": "0x0",
        "chainId": quantity(tx.get("chainId") or chain_id),
        "nonce": quantity(nonce),
        "gasPrice": quantity(gas_price),
        "gas": quantity(tx["gas"]),
        "to": tx["to"],
        "value": quantity(tx.get("value") or 0),
        "data": tx.get("data") or "0x",
        "from": account,
    }


def make_plan(chain: str, account: str, trade: dict, gas_price: int, tip_wei: int, tip_min: int, label: str, broadcast: bool = False) -> dict:
    """The transactions the user's signer signs, in order and with their nonces, plus how `submit` sends them.
    Bundles (BSC): when there is a tip, one tip transfer per builder at nonce n (only the builder that includes its bundle
    gets paid, the other tips die with the nonce), then the trade at n+1…; with a zero tip the bundle is the trade alone,
    from n. A plain broadcast (`rpc`: chains without builders, or --broadcast): the trade from n."""
    chain_id = CHAINS[chain]["id"]
    signer_state = trade.get("signer") or {}
    nonce = signer_state.get("nonce")
    if not isinstance(nonce, int) or isinstance(nonce, bool) or nonce < 0:
        raise ValueError("the service gave no nonce for the account")
    steps = trade.get("steps") or []
    if not steps:
        raise ValueError("the service built no transactions")
    submission = trade.get("submission") or {}
    providers = [p for p in submission.get("providers") or [] if isinstance(p, dict) and p.get("id") and ADDRESS.match(str(p.get("tipTo") or ""))]
    mode = "bundle" if submission.get("mode") == "bundle" and providers and not broadcast else "rpc"
    tipped = mode == "bundle" and tip_wei > 0
    transactions = []
    if tipped:
        for p in providers:
            transactions.append({"index": len(transactions), "kind": "tip", "provider": p["id"], "tx": unsigned({"to": p["tipTo"], "gas": TIP_GAS, "value": tip_wei, "data": "0x"}, nonce, gas_price, chain_id, account)})
    first = nonce + 1 if tipped else nonce
    for i, step in enumerate(steps):
        transactions.append({"index": len(transactions), "kind": step.get("kind"), "description": step.get("description"), "tx": unsigned(step.get("tx") or {}, first + i, gas_price, chain_id, account)})
    return {
        "chain": chain,
        "chainId": chain_id,
        "account": account,
        "label": label,
        "mode": mode,
        "nonce": nonce,
        "gasPriceWei": str(gas_price),
        "tipWei": str(tip_wei if mode == "bundle" else 0),
        "tipMinWei": str(tip_min if mode == "bundle" else 0),
        "providers": [{"id": p["id"], "name": p.get("name"), "tipTo": p["tipTo"]} for p in providers] if mode == "bundle" else [],
        "maxBlocks": submission.get("maxBlocks"),
        "head": trade.get("head"),
        "deadline": trade.get("deadline"),
        "builtAt": int(time.time() * 1000),
        "transactions": transactions,
    }


def load_plan(path: str) -> dict:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    plan = doc.get("plan") if isinstance(doc, dict) and isinstance(doc.get("plan"), dict) else doc
    if not isinstance(plan, dict) or not isinstance(plan.get("transactions"), list) or not plan["transactions"] or plan.get("chain") not in CHAINS:
        raise ValueError("--plan must be the JSON printed by `build` (or its `plan` object)")
    return plan


def read_signed(values: list, path: str | None) -> list[str]:
    raws = [str(v) for v in values or []]
    if path:
        text = Path(path).read_text(encoding="utf-8")
        try:
            loaded = json.loads(text)
        except ValueError:
            loaded = [line.strip() for line in text.splitlines() if line.strip()]
        if isinstance(loaded, dict):
            loaded = loaded.get("signed") or loaded.get("txs") or loaded.get("raw")
        if not isinstance(loaded, list):
            raise ValueError("--signed-file holds a JSON list of signed transactions, or one per line")
        raws.extend(str(v) for v in loaded)
    for raw in raws:
        if not HEX.match(raw) or len(raw) <= 2:
            raise ValueError(f"not a signed transaction in hex: {raw[:24]}…")
    return [raw.lower() for raw in raws]


def check_signed(plan: dict, raws: list[str]) -> list[dict]:
    """Every signed transaction must be the plan's transaction at that position, field by field; returns them with their hashes."""
    expected = plan["transactions"]
    if len(raws) != len(expected):
        raise ValueError(f"the plan has {len(expected)} transactions but {len(raws)} signed ones were given; pass them in the plan's order (tips first, then the trade)")
    signed = []
    for entry, raw in zip(expected, raws):
        tx = entry.get("tx") or {}
        got = decode_signed(raw)
        where = f"transaction {entry.get('index')} ({entry.get('kind')})"
        for field in ("chainId", "nonce", "gasPrice", "gas", "value"):
            if got[field] != as_int(tx.get(field)):
                raise ValueError(f"{where}: {field} is {got[field]}, the plan says {as_int(tx.get(field))}; sign the plan's transactions as they are, in order")
        if (got["to"] or "").lower() != str(tx.get("to") or "").lower():
            raise ValueError(f"{where}: to is {got['to']}, the plan says {tx.get('to')}")
        if got["data"].lower() != str(tx.get("data") or "0x").lower():
            raise ValueError(f"{where}: data differs from the plan")
        signed.append({**entry, "raw": raw, "hash": got["hash"]})
    return signed


def submission_body(plan: dict, signed: list[dict]) -> dict:
    tips = [s for s in signed if s.get("kind") == "tip"]
    trade = [s for s in signed if s.get("kind") != "tip"]
    if not trade:
        raise ValueError("the plan has no trade transactions")
    if plan.get("mode") == "bundle":
        reverting = [s["hash"] for s in trade]
        if tips:
            return {"bundles": [{"provider": t.get("provider"), "txs": [t["raw"]] + [s["raw"] for s in trade], "revertingTxHashes": reverting} for t in tips]}
        providers = [p.get("id") for p in plan.get("providers") or [] if isinstance(p, dict) and p.get("id")]
        if not providers:
            raise ValueError("a bundle plan names no builders")
        return {"bundles": [{"provider": provider, "txs": [s["raw"] for s in trade], "revertingTxHashes": reverting} for provider in providers]}  # no tip: the trade alone, to every builder
    return {"rpc": {"txs": [s["raw"] for s in trade]}}


def parse_sse(stream):
    event, data = None, []
    for line in stream:
        if isinstance(line, bytes):
            line = line.decode("utf-8", "replace")
        line = line.rstrip("\r\n")
        if line == "":
            if data:
                try:
                    payload = json.loads("\n".join(data))
                except ValueError:
                    payload = {"text": "\n".join(data)}
                yield event or "message", payload
            event, data = None, []
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())


def watch(chain: str, hashes: list, timeout_s: float, token: str) -> dict:
    hashes = [h for h in hashes if TX_HASH.match(h or "")][:4]
    if not hashes:
        raise ValueError("watch needs 1–4 transaction hashes")
    timeout_ms = int(min(15, max(0.5, timeout_s)) * 1000)
    url = chain_url(chain, "watch") + "?" + urlencode([("hash", h) for h in hashes] + [("timeoutMs", str(timeout_ms))])
    stream = http("GET", url, token=token, stream=True, timeout=timeout_s + 10)
    receipts: dict = {}
    pending = {h.lower() for h in hashes}
    for event, payload in parse_sse(stream):
        if event == "receipt" and isinstance(payload, dict) and payload.get("hash"):
            receipts[payload["hash"].lower()] = payload
            pending.discard(payload["hash"].lower())
        elif event in ("end", "error"):
            if event == "error":
                receipts.setdefault("_error", payload)
            break
    close = getattr(stream, "close", None)
    if callable(close):
        close()
    error = receipts.pop("_error", None)
    out = {"receipts": list(receipts.values()), "pending": sorted(pending)}
    if error:
        out["error"] = error
    return out


# ----------------------------------------------------------------------------- commands
def emit(obj) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def cmd_auth(args) -> int:
    if os.environ.get("NL_TRADE_TOKEN") and not args.principal:
        emit({"source": "NL_TRADE_TOKEN", "kind": "principal" if os.environ["NL_TRADE_TOKEN"].startswith("nlp_") else "static"})
        return 0
    cached = None if args.refresh else cached_token()
    record = cached or obtain_principal_token()
    emit({"source": "cached" if cached else "issued", "kind": "principal", "principal": record.get("principal"), "expiresAt": record.get("expiresAt")})
    return 0


def cmd_health(args) -> int:
    emit({"chain": args.chain, "health": get(args.chain, "health", None, credentials())})
    return 0


def cmd_head(args) -> int:
    emit({"chain": args.chain, **get(args.chain, "head", None, credentials())})
    return 0


def cmd_prices(args) -> int:
    params = {"symbols": args.symbols} if args.symbols else None
    emit(get("bsc", "prices", params, credentials()))
    return 0


def cmd_token(args) -> int:
    if not ADDRESS.match(args.address):
        raise ValueError("address must be a 0x address")
    token = credentials()
    out = {"chain": args.chain, "meta": token_meta(args.chain, args.address, token)}
    if args.full:
        out["record"] = http("GET", chain_url(args.chain, f"token/{args.address}"), token=token)
    emit(out)
    return 0


def cmd_tokens(args) -> int:
    params = {k: v for k, v in (("platform", args.platform), ("status", args.status), ("limit", str(args.limit))) if v}
    emit({"chain": args.chain, "tokens": get(args.chain, "tokens", params, credentials())})
    return 0


def cmd_quote(args) -> int:
    token = credentials()
    params, pay = trade_params(args, args.chain, token)
    q = get(args.chain, "quote", params, token)
    summary = summarize_quote(args.chain, q, token, pay)
    if not args.account:
        # price only: the service builds nothing without the signing wallet, and says so in execution.error
        summary["executable"] = None
        summary["error"] = None
        summary["note"] = "price only: pass --account to see whether the order is executable for that wallet, or run build"
    out = {"summary": summary}
    if args.raw:
        out["raw"] = q
    emit(out)
    return 0


def cmd_build(args) -> int:
    """Have the service build and simulate the order for the user's wallet; print the price and the plan for the user's signer."""
    token = credentials()
    params, pay = trade_params(args, args.chain, token)
    meta = token_meta(args.chain, args.token, token)
    trade = get(args.chain, "trade", params, token)  # built and simulated for that wallet now; a 409 ends here, nothing to sign
    if not trade.get("steps"):
        raise ApiError(409, {"error": trade.get("error") or "no executable route", "quote": trade.get("quote")})
    summary = summarize_trade(args.chain, trade, token, pay)
    label = order_label(params["side"], meta.get("symbol") or short_address(args.token), summary.get("amountIn"), params.get("percent"))
    tip_wei, tip_min = tip_for(args, trade)
    plan = make_plan(args.chain, args.account, trade, gas_price_for(args, trade), tip_wei, tip_min, label, broadcast=args.broadcast)
    out = {"summary": summary, "plan": plan}
    if args.raw:
        out["raw"] = trade
    if args.out:
        Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    emit(out)
    return 0


def cmd_submit(args) -> int:
    token = credentials()
    if args.plan:
        plan = load_plan(args.plan)
        deadline = plan.get("deadline")
        if deadline and time.time() > as_int(deadline):
            raise ValueError("the plan's deadline has passed; run build again (nonce, gas price and tip are only right for a moment)")
        signed = check_signed(plan, read_signed(args.signed, args.signed_file))
        result = post(plan["chain"], "submit", submission_body(plan, signed), token)
        trade_hashes = [s["hash"] for s in signed if s.get("kind") != "tip"]
        emit({"chain": plan["chain"], "mode": plan.get("mode"), "label": plan.get("label"), "transactions": [{"index": s.get("index"), "kind": s.get("kind"), "provider": s.get("provider"), "hash": s["hash"]} for s in signed], "watch": trade_hashes, **result})
        return 0
    if args.signed or args.signed_file:
        raise ValueError("--signed goes with --plan; transactions signed outside a plan are sent with --broadcast or --bundles")
    if args.bundles:
        bundles = json.loads(Path(args.bundles).read_text(encoding="utf-8"))
        if not isinstance(bundles, list) or not bundles:
            raise ValueError("--bundles must be a JSON list of {provider, txs, revertingTxHashes?}")
        result = post(args.chain, "submit", {"bundles": bundles}, token)
    elif args.broadcast:
        raws = read_signed(args.broadcast, None)
        if len(raws) > MAX_BROADCAST:
            raise ValueError(f"--broadcast takes 1–{MAX_BROADCAST} signed transactions")
        result = post(args.chain, "submit", {"rpc": {"txs": raws}}, token)
    else:
        raise ValueError("submit needs --plan with --signed, or --broadcast, or --bundles")
    emit({"chain": args.chain, **result})
    return 0


def cmd_watch(args) -> int:
    emit({"chain": args.chain, **watch(args.chain, args.hash, args.timeout, credentials())})
    return 0


# ----------------------------------------------------------------------------- argument parsing
def add_common(parser, chain=True):
    if chain:
        parser.add_argument("--chain", choices=sorted(CHAINS), default="bsc", help="bsc (default) or robinhood")


def add_order(parser, account_required=False):
    parser.add_argument("--token", required=True, help="token contract address")
    parser.add_argument("--side", required=True, choices=["buy", "sell"])
    amount = parser.add_mutually_exclusive_group()
    amount.add_argument("--amount", help="buy: the native coin (or the --pay stablecoin) to spend; sell: tokens to sell; human units")
    amount.add_argument("--amount-wei", help="the same in the coin's smallest unit")
    amount.add_argument("--percent", help="share of the wallet's balance, 0–100 with up to two decimals (needs --account)")
    parser.add_argument("--account", required=account_required, help="the user's wallet: the address that signs and pays" + ("" if account_required else " (optional for a quote)"))
    parser.add_argument("--recipient", help="who receives the output (default: the account)")
    parser.add_argument("--slippage", help="percent, 0–50 (service default 1)")
    parser.add_argument("--deadline", type=int, help="router deadline in seconds, 30–3600 (service default 120)")
    parser.add_argument("--pay", help="buys: pay with this stablecoin instead of the native coin (USDC / USD1 / USDT on bsc, USDG on robinhood)")
    parser.add_argument("--payout", choices=["native", "quote"], help="sells: receive the native coin (default) or the platform's quote coin")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dex_trade.py", description=__doc__.splitlines()[0])
    p.add_argument("--version", action="version", version=VERSION)
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("auth", help="make sure a service credential is available (prints its kind and expiry, never the token)")
    s.add_argument("--principal", action="store_true", help="use the skill's own principal even when NL_TRADE_TOKEN is set")
    s.add_argument("--refresh", action="store_true", help="obtain a fresh principal token")
    s.set_defaults(func=cmd_auth)

    for name, func, helptext in (("health", cmd_health, "service health for a chain"), ("head", cmd_head, "the chain head the service sees")):
        s = sub.add_parser(name, help=helptext)
        add_common(s)
        s.set_defaults(func=func)

    s = sub.add_parser("prices", help="USD prices of the major coins (BNB, ETH, SOL, BTC …)")
    s.add_argument("--symbols", help="comma-separated, e.g. BNB,ETH")
    add_common(s, chain=False)
    s.set_defaults(func=cmd_prices)

    s = sub.add_parser("token", help="what the service knows about a token: symbol, decimals, platform, status, logo")
    s.add_argument("address")
    s.add_argument("--full", action="store_true", help="also the registry record (curve state, pools)")
    add_common(s)
    s.set_defaults(func=cmd_token)

    s = sub.add_parser("tokens", help="recently traded tokens the service tracks")
    s.add_argument("--platform")
    s.add_argument("--status", choices=["curve", "graduated"])
    s.add_argument("--limit", type=int, default=20)
    add_common(s)
    s.set_defaults(func=cmd_tokens)

    s = sub.add_parser("quote", help="price a buy or sell (with --account the service also says whether it is executable for that wallet)")
    add_order(s)
    s.add_argument("--raw", action="store_true", help="include the service's full response")
    add_common(s)
    s.set_defaults(func=cmd_quote)

    s = sub.add_parser("build", help="have the service build and simulate the order for the user's wallet; print the plan (unsigned transactions) for the user's own signer")
    add_order(s, account_required=True)
    s.add_argument("--tip-wei", help="builder tip in wei (bsc bundles; default: the service's market suggestion; 0 = bundles without a tip transfer)")
    s.add_argument("--gas-price-wei", help="gas price in wei (default: max(node, market) × 1.2)")
    s.add_argument("--broadcast", action="store_true", help="plan a plain broadcast instead of builder bundles")
    s.add_argument("--out", help="also write the output to this JSON file (what submit --plan reads)")
    s.add_argument("--raw", action="store_true", help="include the service's full response")
    add_common(s)
    s.set_defaults(func=cmd_build)

    s = sub.add_parser("submit", help="forward signed transactions: the plan's (--plan + --signed, checked against the plan), or ones signed elsewhere (--broadcast / --bundles)")
    s.add_argument("--plan", help="the JSON written by build (--out) or its plan object")
    s.add_argument("--signed", nargs="*", default=[], help="the plan's transactions, signed, in the plan's order (raw hex)")
    s.add_argument("--signed-file", help="the same from a file: a JSON list, or one per line")
    s.add_argument("--broadcast", nargs="*", default=[], help=f"signed raw transactions in hex to broadcast in order, 1–{MAX_BROADCAST}")
    s.add_argument("--bundles", help="JSON file with a list of {provider, txs, revertingTxHashes?}")
    add_common(s)
    s.set_defaults(func=cmd_submit)

    s = sub.add_parser("watch", help="wait for receipts (up to 4 hashes)")
    s.add_argument("--hash", nargs="+", required=True)
    s.add_argument("--timeout", type=float, default=5.0, help="seconds, 0.5–15")
    add_common(s)
    s.set_defaults(func=cmd_watch)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return args.func(args)
    except ApiError as e:
        if e.status and e.status >= 400:
            emit({"status": e.status, **e.payload})
            return 2
        sys.stderr.write(f"error: {e.payload.get('error') or e}\n")
        return 1
    except (ValueError, OSError, KeyError, TypeError) as e:
        sys.stderr.write(f"error: {e}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
