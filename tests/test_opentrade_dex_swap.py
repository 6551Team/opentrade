"""Tests for opentrade-dex-swap/scripts/dex_trade.py: run with `python -m unittest discover -s tests -v` from the repository root.

The signed transactions below were produced offline with Foundry's `cast mktx --legacy` and the public Anvil test key
(account 0xf39f…2266); their hashes are `cast keccak` of the raw bytes. No service is contacted: `urlopen` is replaced.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "opentrade-dex-swap"
SCRIPT = SKILL / "scripts" / "dex_trade.py"
SPEC = importlib.util.spec_from_file_location("dex_trade", SCRIPT)
dex = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dex)

TOKEN = "0x" + "ab" * 20
ACCOUNT = "0x" + "11" * 20
ROUTER = "0x" + "22" * 20
TIP_TO_A = "0x" + "aa" * 20
TIP_TO_B = "0x" + "bb" * 20
SECRET = "nlp_test.secret-service-token"
META = {"address": TOKEN, "symbol": "TEST", "name": "Test Coin", "decimals": 9, "platform": "fourmeme", "status": "curve", "logo": "https://logo.test/t.png"}
APPROVE_DATA = "0x095ea7b3000000000000000000000000222222222222222222222222222222222222222200000000000000000000000000000000000000000000000000000002e90edd00"

# cast mktx --legacy --chain 56 --gas-price 120000000 … with the Anvil test key; keyed by what the plan expects of them.
TIP_A = {  # nonce 17, 21000 gas, 0.003 BNB to TIP_TO_A
    "raw": "0xf86b118407270e0082520894aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa870aa87bee538000808193a0464775bccd7ab1f3843087cb263e581d23e699ea67963a2b4423630eb79a45a2a031566cc548445f22eafcb4d1b577ea0b86041e6e1a7d27115ebaa4f17b523a63",
    "hash": "0x4631f2969c602ebb32ce717d68d5001160c2697c91ba774304ff59a639d91a33",
}
TIP_B = {  # nonce 17, 21000 gas, 0.003 BNB to TIP_TO_B
    "raw": "0xf86b118407270e0082520894bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb870aa87bee538000808194a0fb590e56b6fa38892c197dc2a4a9ab52fc6226888760956309e72f5327c7c429a00ddb40194c5e9dfbbd4dc703b5d06b279c6c7ee97143ab12180f2b7c39c4b577",
    "hash": "0x3f505143e45f91a4d28ab9367468481a8e4a02412861da6c6bb23e18e5914895",
}
APPROVE = {  # nonce 18, 60000 gas, to TOKEN, APPROVE_DATA
    "raw": "0xf8a9128407270e0082ea6094abababababababababababababababababababab80b844095ea7b3000000000000000000000000222222222222222222222222222222222222222200000000000000000000000000000000000000000000000000000002e90edd008194a0f960075e8170c329aee3f0485a62ac322df6fa0b61931b4c07902f05b38a9f4ca00b77da84ca103b749c4fdc6250b65a1556c20c53723fa90dabd2dc896fb20586",
    "hash": "0xb5fae00e22a45385d51d076438decdac1e01e7480847b65c82e0e81a7c0240fd",
}
SELL = {  # nonce 19, 420000 gas, to ROUTER, data 0x02
    "raw": "0xf865138407270e00830668a094222222222222222222222222222222222222222280028194a003122e97f07b7e4e5dae9c26e924f44c0219eff3d9ed0403b86bafafa40d8b3da079a78ac059fccb187075b23d89f71b62b1ec218fbc12df9df0ff0897722cbe09",
    "hash": "0xc8bcd65b563591425b669fab9372735b9c384edb8f787d21f22c7eae1dce6589",
}
BUY = {  # nonce 18, 420000 gas, to ROUTER, 0.1 BNB, data 0x01
    "raw": "0xf86d128407270e00830668a094222222222222222222222222222222222222222288016345785d8a0000018194a09b6b8be41cd3b5cf05f4a17c310ccfa1a0f2cda890bf03160a2165b92010030ba05b1d3d308b0bcfee148eda14e986ba607e42b82e710e208da944386bebdda547",
    "hash": "0xbd840fec19cad862996845593d315cfbfe0f543b712a8704df0bb5c88f6b309b",
}
TYPED = "0x02f8663811018407270e0082520894aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa0180c001"  # an EIP-1559 envelope, truncated: rejected by its type byte


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        self.close()


def submission():
    return {"mode": "bundle", "providers": [{"id": "blockrazor", "name": "BlockRazor", "tipTo": TIP_TO_A}, {"id": "48club", "name": "48 Club", "tipTo": TIP_TO_B}], "tip": {"suggestedWei": "3000000000000000", "minWei": "0"}, "maxBlocks": 10}


def quote_body(side="buy", amount_in="100000000000000000", with_execution=True):
    body = {
        "token": TOKEN, "platform": "fourmeme", "status": "curve", "quoteToken": dex.ZERO, "side": side,
        "amountIn": amount_in, "amountInToken": dex.ZERO if side == "buy" else TOKEN, "quoteDecimals": 18, "block": "70000000", "ageMs": 12, "head": "70000001",
        "indicative": {"amountOut": "5000000000000", "amountOutToken": TOKEN if side == "buy" else dex.ZERO, "source": "curve", "block": "70000000"},
        "submission": submission(),
    }
    if with_execution:
        body["execution"] = {"provider": "router", "amountOut": "5000000000000", "minOut": "4950000000000", "slippageBps": 100, "slippageProtected": True, "route": ["fourmeme"], "tx": {"chainId": 56, "from": ACCOUNT, "to": ROUTER, "data": "0x01", "value": amount_in, "gas": "400000"}, "payoutToken": dex.ZERO}
    else:
        body["execution"] = {"error": "account 缺失"}  # what the service says when no wallet was given
    return body


def trade_body(side="sell", nonce=17, mode="bundle"):
    """What /trade answers: a sell is approve + sell (nonces n+1, n+2 after the tips), a native buy is a single step."""
    if side == "sell":
        steps = [
            {"kind": "approve", "description": "approve router", "tx": {"chainId": 56, "from": ACCOUNT, "to": TOKEN, "data": APPROVE_DATA, "value": "0", "gas": "60000", "gasSource": "simulate"}},
            {"kind": "sell", "description": "sell via router", "tx": {"chainId": 56, "from": ACCOUNT, "to": ROUTER, "data": "0x02", "value": "0", "gas": "420000", "gasSource": "simulate"}},
        ]
        amount_in, amount_in_token, expected_out, expected_out_token, min_out = "12500000000", TOKEN, "70000000000000000", dex.ZERO, "69300000000000000"
    else:
        steps = [{"kind": "buy", "description": "buy via router", "tx": {"chainId": 56, "from": ACCOUNT, "to": ROUTER, "data": "0x01", "value": "100000000000000000", "gas": "420000", "gasSource": "simulate"}}]
        amount_in, amount_in_token, expected_out, expected_out_token, min_out = "100000000000000000", dex.ZERO, "5000000000000", TOKEN, "4950000000000"
    return {
        "token": TOKEN, "platform": "fourmeme", "status": "curve", "head": "70000001", "side": side, "amountIn": amount_in, "amountInToken": amount_in_token,
        "quoteToken": dex.ZERO, "quoteDecimals": 18, "expectedOut": expected_out, "expectedOutToken": expected_out_token, "minOut": min_out,
        "slippageBps": 100, "slippagePercent": "1", "slippageProtected": True, "payoutToken": dex.ZERO, "provider": "router", "deadline": "1763600000",
        "steps": steps, "submission": {**submission(), "mode": mode}, "signer": {"address": ACCOUNT, "nonce": nonce, "balance": "1000000000000000000", "gasPrice": "100000000"},
        "simulation": {"ok": True, "block": "70000000", "ms": 40, "calls": [{"kind": s["kind"], "status": "success", "gasUsed": "46000"} for s in steps]},
        "tip": {"suggestedWei": "3000000000000000", "floorWei": "0", "p50Wei": "1", "p90Wei": "2", "basis": "market", "gasPriceWei": "100000000", "gasBasis": "market"},
        "serverMs": 55, "quote": quote_body(side, amount_in),
    }


def sse(events):
    return Response("".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events).encode())


class Fake:
    """urlopen stand-in: answers by path, records every request."""

    def __init__(self, routes):
        self.routes = routes
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append((request, timeout))
        path = urlsplit(request.full_url).path
        for suffix, answer in self.routes.items():
            if path.endswith(suffix):
                if callable(answer):
                    answer = answer(request)
                if isinstance(answer, Exception):
                    raise answer
                if isinstance(answer, io.BytesIO):
                    return answer
                return Response(json.dumps(answer).encode())
        raise AssertionError(f"unexpected request {request.full_url}")

    def request(self, suffix):
        for request, _timeout in self.requests:
            if urlsplit(request.full_url).path.endswith(suffix):
                return request
        raise AssertionError(f"no request to {suffix}")

    def paths(self):
        return [urlsplit(request.full_url).path.rsplit("/", 1)[-1] for request, _timeout in self.requests]


class FakeSession:
    """The kept-alive login connection, answered by the same route table."""

    def __init__(self, fake):
        self.fake = fake

    def post(self, url, body):
        from urllib.request import Request

        response = self.fake(Request(url, data=json.dumps(body).encode(), method="POST"))
        return json.loads(response.read())

    def close(self):
        pass


def http_error(code, body):
    return HTTPError("https://example/x", code, "refused", {}, io.BytesIO(json.dumps(body).encode()))


def run(argv, env=None):
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.dict(os.environ, {"NL_TRADE_TOKEN": SECRET, **(env or {})}), mock.patch.object(dex.sys, "stdout", out), mock.patch.object(dex.sys, "stderr", err):
        code = dex.main(argv)
    return code, out.getvalue(), err.getvalue()


def build(argv, routes, env=None):
    fake = Fake(routes)
    dex._meta_cache.clear()  # token facts are cached per process; every build here starts cold
    with mock.patch.object(dex, "urlopen", fake):
        code, out, err = run(["build", *argv], env)
    return fake, code, out, err


def plan_file(tmp, side="sell", nonce=17, mode="bundle", deadline=None, extra=()):
    """A plan as `build` writes it, with a deadline that has not passed unless told otherwise."""
    _fake, code, out, err = build(["--token", TOKEN, "--side", side, "--amount", "12.5" if side == "sell" else "0.1", "--account", ACCOUNT, *extra], {"/bsc/meta": META, "/bsc/trade": trade_body(side, nonce, mode)})
    assert code == 0, err
    doc = json.loads(out)
    doc["plan"]["deadline"] = str(int(time.time()) + 100) if deadline is None else deadline
    path = Path(tmp) / "order.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path, doc["plan"]


def submit(argv, fake=None):
    bodies = []

    def route(request):
        bodies.append(json.loads(request.data))
        return {"head": "70000002", "maxBlockNumber": "70000012", "results": [{"provider": "blockrazor", "bundleHash": "0x" + "e1" * 32}], "hashes": [TIP_A["hash"], APPROVE["hash"], SELL["hash"]]}

    fake = fake or Fake({"/bsc/submit": route})
    with mock.patch.object(dex, "urlopen", fake):
        code, out, err = run(["submit", *argv])
    return bodies, code, out, err


class SkillDocumentTests(unittest.TestCase):
    def test_frontmatter_routing_and_the_no_key_rule(self) -> None:
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        match = re.match(r"^---\r?\n([\s\S]*?)\r?\n---\r?\n", text)
        self.assertIsNotNone(match)
        self.assertIn("name: opentrade-dex-swap", match.group(1))
        self.assertIn("description:", match.group(1))
        self.assertIn('version: "2.0.0"', match.group(1))
        for fragment in ("opentrade-cex", "opentrade-gateway", "NL_TRADE_TOKEN", "references/trade-service-api.md", "scripts/dex_trade.py quote", "scripts/dex_trade.py build", "scripts/dex_trade.py submit", "scripts/dex_trade.py watch", "--plan", "--signed", "type 0", "private key", "robinhood", "bsc", "revertingTxHashes"):
            self.assertIn(fragment, text, fragment)
        for banned in ("NL_TRADE_PRIVATE_KEY", "--key-file", "trade.sign", "newsliquid-action", "RenderPageActions", "opentrade swap quote", "hot wallet"):
            self.assertNotIn(banned, text, banned)
        reference = (SKILL / "references" / "trade-service-api.md").read_text(encoding="utf-8")
        for fragment in ("transactions", "revertingTxHashes", "tipTo", "type 0", "gasPrice", "deadline", "/submit", "/watch"):
            self.assertIn(fragment, reference, fragment)
        self.assertNotIn("trade.sign", reference)
        script = SCRIPT.read_text(encoding="utf-8")
        for banned in ("NL_TRADE_PRIVATE_KEY", "eth_account", "sign_transaction", "key_file"):
            self.assertNotIn(banned, script, banned)
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("opentrade-dex-swap", readme)
        self.assertIn("Robinhood", readme)

    def test_cli_has_no_signing_commands_or_key_options(self) -> None:
        p = dex.parser()
        for argv in (["sign", "--plan", "x"], ["execute", "--token", TOKEN, "--side", "buy", "--amount", "1"], ["build", "--token", TOKEN, "--side", "buy", "--amount", "1", "--account", ACCOUNT, "--key-file", "k"], ["build", "--token", TOKEN, "--side", "buy", "--amount", "1"]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit), mock.patch.object(dex.sys, "stderr", io.StringIO()):
                p.parse_args(argv)
        self.assertEqual(sorted(p._subparsers._group_actions[0].choices), ["auth", "build", "head", "health", "prices", "quote", "submit", "token", "tokens", "watch"])


class UnitsTests(unittest.TestCase):
    def test_amounts_and_percentages(self) -> None:
        self.assertEqual(dex.to_units("0.1", 18), 10**17)
        self.assertEqual(dex.to_units("12.5", 9), 12_500_000_000)
        self.assertEqual(dex.to_units("1", 0), 1)
        for bad in ("0", "-1", "abc", "0.0000000001"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                dex.to_units(bad, 9)
        self.assertEqual(dex.from_units("12500000000", 9), "12.5")
        self.assertEqual(dex.from_units(10**18, 18), "1")
        self.assertEqual(dex.from_units("5000000000000", 9), "5000")
        self.assertEqual(dex.percent_text("50"), "50")
        self.assertEqual(dex.percent_text("12.50"), "12.5")
        for bad in ("0", "100.5", "1.234", "x"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                dex.percent_text(bad)
        self.assertEqual(dex.slippage_text("0.50"), "0.5")
        with self.assertRaises(ValueError):
            dex.slippage_text("51")

    def test_quantities_and_labels(self) -> None:
        self.assertEqual(dex.as_int("0x11"), 17)
        self.assertEqual(dex.as_int("17"), 17)
        self.assertEqual(dex.as_int(17), 17)
        for bad in (True, "x", "1.5", None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                dex.as_int(bad)
        self.assertEqual(dex.quantity(0), "0x0")
        self.assertEqual(dex.quantity("120000000"), "0x7270e00")
        self.assertEqual(dex.quantity("0x5208"), "0x5208")
        with self.assertRaises(ValueError):
            dex.quantity(-1)
        self.assertEqual(dex.order_label("buy", "TEST", {"human": "0.1", "symbol": "BNB"}, None), "buy TEST with 0.1 BNB")
        self.assertEqual(dex.order_label("sell", "TEST", None, "50"), "sell 50% TEST")
        self.assertEqual(dex.order_label("sell", "TEST", {"human": "12.5"}, None), "sell 12.5 TEST")

    def test_keccak_and_signed_transaction_decoding(self) -> None:
        self.assertEqual(dex.keccak256(b"").hex(), "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470")
        self.assertEqual(dex.keccak256(b"abc").hex(), "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45")
        self.assertEqual(dex.keccak256(b"x" * 500).hex(), "3bb5100d54bc21634816af24118e7fed25f82b4e9df4300f4573303d67d0dba7")  # more than one block, checked against cast
        tip = dex.decode_signed(TIP_A["raw"])
        self.assertEqual(tip, {"nonce": 17, "gasPrice": 120000000, "gas": 21000, "to": TIP_TO_A, "value": 3000000000000000, "data": "0x", "chainId": 56, "hash": TIP_A["hash"]})
        approve = dex.decode_signed(APPROVE["raw"])
        self.assertEqual((approve["nonce"], approve["gas"], approve["to"], approve["value"], approve["data"], approve["hash"]), (18, 60000, TOKEN, 0, APPROVE_DATA, APPROVE["hash"]))
        self.assertEqual(dex.decode_signed(BUY["raw"])["value"], 10**17)
        self.assertEqual(dex.decode_signed(SELL["raw"].upper().replace("0X", "0x"))["hash"], SELL["hash"])
        for bad in (TYPED, "0x", "zz", "0xf86b11", "0x80", SELL["raw"] + "00"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                dex.decode_signed(bad)
        self.assertEqual(dex.rlp_decode(bytes.fromhex("c88363617483646f67")), [b"cat", b"dog"])
        self.assertEqual(dex.rlp_decode(b"\x80"), b"")


class QuoteCommandTests(unittest.TestCase):
    def test_buy_quote_sends_wei_and_bearer_and_keeps_the_token_out_of_the_output(self) -> None:
        fake = Fake({"/bsc/meta": META, "/bsc/quote": quote_body(with_execution=False)})
        with mock.patch.object(dex, "urlopen", fake):
            code, out, err = run(["quote", "--token", TOKEN, "--side", "buy", "--amount", "0.1", "--slippage", "0.50"])
        self.assertEqual(code, 0, err)
        request = fake.request("/bsc/quote")
        query = parse_qs(urlsplit(request.full_url).query)
        self.assertEqual(query["amountIn"], ["100000000000000000"])
        self.assertEqual(query["side"], ["buy"])
        self.assertEqual(query["slippage"], ["0.5"])
        self.assertNotIn("account", query)
        self.assertEqual(request.get_header("Authorization"), f"Bearer {SECRET}")
        self.assertEqual(request.get_header("User-agent"), dex.USER_AGENT)
        self.assertTrue(request.full_url.startswith("https://trade-service.newsliquid.com/v2/bsc/quote?"))
        self.assertNotIn(SECRET, out + err)
        summary = json.loads(out)["summary"]
        self.assertEqual(summary["amountIn"], {"raw": "100000000000000000", "human": "0.1", "token": dex.ZERO, "symbol": "BNB"})
        self.assertEqual(summary["expectedOut"]["human"], "5000")
        self.assertEqual(summary["expectedOut"]["symbol"], "TEST")
        self.assertIsNone(summary["minOut"])
        self.assertIsNone(summary["executable"])  # no --account: a price, not a plan
        self.assertIsNone(summary["error"])
        self.assertIn("--account", summary["note"])
        self.assertEqual(summary["submission"]["providers"], ["blockrazor", "48club"])
        fake = Fake({"/bsc/meta": META, "/bsc/quote": quote_body()})
        with mock.patch.object(dex, "urlopen", fake):
            code, out, _err = run(["quote", "--token", TOKEN, "--side", "buy", "--amount", "0.1", "--account", ACCOUNT])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["summary"]["executable"])
        self.assertEqual(json.loads(out)["summary"]["minOut"]["human"], "4950")

    def test_sell_amount_uses_token_decimals_percent_needs_account_and_stable_buys_send_input(self) -> None:
        fake = Fake({"/bsc/meta": META, "/bsc/quote": quote_body("sell", "12500000000")})
        with mock.patch.object(dex, "urlopen", fake):
            code, out, _err = run(["quote", "--token", TOKEN, "--side", "sell", "--amount", "12.5", "--account", ACCOUNT, "--payout", "quote"])
        self.assertEqual(code, 0)
        query = parse_qs(urlsplit(fake.request("/bsc/quote").full_url).query)
        self.assertEqual(query["amountIn"], ["12500000000"])
        self.assertEqual(query["account"], [ACCOUNT])
        self.assertEqual(query["payout"], ["quote"])
        self.assertEqual(json.loads(out)["summary"]["amountIn"]["human"], "12.5")

        fake = Fake({"/bsc/meta": META, "/bsc/quote": {**quote_body("sell", "6250000000"), "percent": "50"}})
        with mock.patch.object(dex, "urlopen", fake):
            code, out, _err = run(["quote", "--token", TOKEN, "--side", "sell", "--percent", "50", "--account", ACCOUNT])
        self.assertEqual(code, 0)
        query = parse_qs(urlsplit(fake.request("/bsc/quote").full_url).query)
        self.assertEqual(query["percent"], ["50"])
        self.assertNotIn("amountIn", query)
        self.assertEqual(json.loads(out)["summary"]["percent"], "50")

        code, _out, err = run(["quote", "--token", TOKEN, "--side", "sell", "--percent", "50"])
        self.assertEqual(code, 1)
        self.assertIn("--account", err)

        fake = Fake({"/bsc/meta": META, "/bsc/quote": quote_body()})
        with mock.patch.object(dex, "urlopen", fake):
            code, _out, _err = run(["quote", "--token", TOKEN, "--side", "buy", "--amount", "20", "--pay", "usdc"])
        self.assertEqual(code, 0)
        query = parse_qs(urlsplit(fake.request("/bsc/quote").full_url).query)
        self.assertEqual(query["input"], [dex.STABLES["bsc"]["USDC"][0]])
        self.assertEqual(query["amountIn"], [str(20 * 10**18)])

        code, _out, err = run(["quote", "--chain", "robinhood", "--token", TOKEN, "--side", "buy", "--amount", "1", "--pay", "USDC"])
        self.assertEqual(code, 1)
        self.assertIn("USDG", err)


class BuildCommandTests(unittest.TestCase):
    """`build` has the service build and simulate the order for the user's wallet and prints the plan for the user's own signer."""

    def test_sell_plans_tips_then_the_trade_with_consecutive_nonces_as_json_rpc_objects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "order.json"
            fake, code, out, err = build(["--token", TOKEN, "--side", "sell", "--amount", "12.5", "--account", ACCOUNT, "--slippage", "1", "--out", str(out_path)], {"/bsc/meta": META, "/bsc/trade": trade_body()})
            self.assertEqual(code, 0, err)
            self.assertEqual(json.loads(out_path.read_text(encoding="utf-8")), json.loads(out))
        self.assertEqual(fake.paths(), ["meta", "trade"])
        query = parse_qs(urlsplit(fake.request("/bsc/trade").full_url).query)
        self.assertEqual(query["account"], [ACCOUNT])
        self.assertEqual(query["amountIn"], ["12500000000"])
        self.assertNotIn(SECRET, out + err)
        result = json.loads(out)
        summary = result["summary"]
        self.assertTrue(summary["executable"])
        self.assertEqual(summary["expectedOut"], {"raw": "70000000000000000", "human": "0.07", "token": dex.ZERO, "symbol": "BNB"})
        self.assertEqual(summary["minOut"]["human"], "0.0693")
        self.assertTrue(summary["simulation"]["ok"])
        plan = result["plan"]
        self.assertEqual({k: plan[k] for k in ("chain", "chainId", "account", "label", "mode", "nonce", "gasPriceWei", "tipWei", "maxBlocks", "head", "deadline")}, {"chain": "bsc", "chainId": 56, "account": ACCOUNT, "label": "sell 12.5 TEST", "mode": "bundle", "nonce": 17, "gasPriceWei": "120000000", "tipWei": "3000000000000000", "maxBlocks": 10, "head": "70000001", "deadline": "1763600000"})
        self.assertEqual(plan["providers"], [{"id": "blockrazor", "name": "BlockRazor", "tipTo": TIP_TO_A}, {"id": "48club", "name": "48 Club", "tipTo": TIP_TO_B}])
        self.assertIsInstance(plan["builtAt"], int)
        self.assertEqual([(t["index"], t["kind"], t.get("provider")) for t in plan["transactions"]], [(0, "tip", "blockrazor"), (1, "tip", "48club"), (2, "approve", None), (3, "sell", None)])
        self.assertEqual(plan["transactions"][0]["tx"], {"type": "0x0", "chainId": "0x38", "nonce": "0x11", "gasPrice": "0x7270e00", "gas": "0x5208", "to": TIP_TO_A, "value": "0xaa87bee538000", "data": "0x", "from": ACCOUNT})
        self.assertEqual(plan["transactions"][1]["tx"]["nonce"], "0x11")  # the same nonce: only one builder's tip can land
        self.assertEqual(plan["transactions"][1]["tx"]["to"], TIP_TO_B)
        self.assertEqual(plan["transactions"][2]["tx"], {"type": "0x0", "chainId": "0x38", "nonce": "0x12", "gasPrice": "0x7270e00", "gas": "0xea60", "to": TOKEN, "value": "0x0", "data": APPROVE_DATA, "from": ACCOUNT})
        self.assertEqual(plan["transactions"][3]["tx"]["nonce"], "0x13")
        self.assertEqual(plan["transactions"][3]["tx"]["gas"], "0x668a0")
        self.assertEqual(plan["transactions"][3]["description"], "sell via router")
        self.assertNotIn("raw", result)
        # the plan's transactions are exactly what cast signed for the vectors above
        for entry, vector in zip(plan["transactions"], (TIP_A, TIP_B, APPROVE, SELL)):
            decoded = dex.decode_signed(vector["raw"])
            self.assertEqual({k: dex.as_int(entry["tx"][k]) for k in ("chainId", "nonce", "gasPrice", "gas", "value")}, {k: decoded[k] for k in ("chainId", "nonce", "gasPrice", "gas", "value")})

    def test_broadcast_plans_and_rpc_chains_carry_no_tips(self) -> None:
        _fake, code, out, err = build(["--token", TOKEN, "--side", "buy", "--amount", "0.1", "--account", ACCOUNT, "--broadcast"], {"/bsc/meta": META, "/bsc/trade": trade_body("buy", nonce=18)})
        self.assertEqual(code, 0, err)
        plan = json.loads(out)["plan"]
        self.assertEqual((plan["mode"], plan["tipWei"], plan["providers"], plan["label"]), ("rpc", "0", [], "buy TEST with 0.1 BNB"))
        self.assertEqual([(t["kind"], t["tx"]["nonce"], t["tx"]["value"]) for t in plan["transactions"]], [("buy", "0x12", "0x16345785d8a0000")])

        _fake, code, out, err = build(["--token", TOKEN, "--side", "sell", "--amount", "12.5", "--account", ACCOUNT, "--tip-wei", "0"], {"/bsc/meta": META, "/bsc/trade": trade_body()})
        self.assertEqual(code, 0, err)
        plan = json.loads(out)["plan"]  # a zero tip keeps the builder bundles, just without a tip transfer: the trade starts at the wallet's nonce
        self.assertEqual((plan["mode"], plan["tipWei"], len(plan["providers"])), ("bundle", "0", 2))
        self.assertEqual([(t["kind"], t["tx"]["nonce"]) for t in plan["transactions"]], [("approve", "0x11"), ("sell", "0x12")])

        _fake, code, out, err = build(["--chain", "robinhood", "--token", TOKEN, "--side", "sell", "--amount", "12.5", "--account", ACCOUNT], {"/robinhood/meta": META, "/robinhood/trade": trade_body(mode="rpc")})
        self.assertEqual(code, 0, err)
        plan = json.loads(out)["plan"]
        self.assertEqual((plan["chain"], plan["chainId"], plan["mode"]), ("robinhood", 4663, "rpc"))
        self.assertEqual([t["kind"] for t in plan["transactions"]], ["approve", "sell"])
        self.assertEqual(plan["transactions"][0]["tx"]["nonce"], "0x11")

    def test_tip_and_gas_price_overrides(self) -> None:
        _fake, code, out, err = build(["--token", TOKEN, "--side", "sell", "--amount", "12.5", "--account", ACCOUNT, "--tip-wei", "5000000000000000", "--gas-price-wei", "200000000"], {"/bsc/meta": META, "/bsc/trade": trade_body()})
        self.assertEqual(code, 0, err)
        plan = json.loads(out)["plan"]
        self.assertEqual((plan["tipWei"], plan["tipMinWei"], plan["gasPriceWei"]), ("5000000000000000", "0", "200000000"))
        self.assertEqual(plan["transactions"][0]["tx"]["value"], hex(5000000000000000))
        self.assertEqual(plan["transactions"][2]["tx"]["gasPrice"], hex(200000000))

        floor = trade_body()
        floor["submission"]["tip"]["minWei"] = "1000"
        _fake, code, out, err = build(["--token", TOKEN, "--side", "sell", "--amount", "12.5", "--account", ACCOUNT, "--tip-wei", "1"], {"/bsc/meta": META, "/bsc/trade": floor})
        self.assertEqual(code, 0, err)
        plan = json.loads(out)["plan"]  # a small positive tip is raised to the builders' floor, as the NewsLiquid wallet does
        self.assertEqual((plan["tipWei"], plan["tipMinWei"], plan["transactions"][0]["tx"]["value"]), ("1000", "1000", "0x3e8"))
        _fake, code, _out, err = build(["--token", TOKEN, "--side", "sell", "--amount", "12.5", "--account", ACCOUNT, "--tip-wei", "-1"], {"/bsc/meta": META, "/bsc/trade": floor})
        self.assertEqual(code, 1)
        self.assertIn("negative", err)

        _fake, code, out, err = build(["--token", TOKEN, "--side", "sell", "--percent", "50", "--account", ACCOUNT, "--raw"], {"/bsc/meta": META, "/bsc/trade": {**trade_body(), "percent": "50"}})
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertEqual(result["plan"]["label"], "sell 50% TEST")
        self.assertEqual(result["summary"]["percent"], "50")
        self.assertEqual(result["raw"]["signer"]["nonce"], 17)

    def test_orders_without_a_route_are_refused_and_nothing_is_planned(self) -> None:
        _fake, code, out, _err = build(["--token", TOKEN, "--side", "buy", "--amount", "0.1", "--account", ACCOUNT], {"/bsc/meta": META, "/bsc/trade": http_error(409, {"error": "simulation reverted", "quote": {"token": TOKEN}})})
        self.assertEqual(code, 2)
        payload = json.loads(out)
        self.assertEqual((payload["status"], payload["error"], payload["quote"]["token"]), (409, "simulation reverted", TOKEN))
        self.assertNotIn("plan", payload)

        _fake, code, out, _err = build(["--token", TOKEN, "--side", "buy", "--amount", "0.1", "--account", ACCOUNT], {"/bsc/meta": META, "/bsc/trade": {**trade_body("buy"), "steps": []}})
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["status"], 409)

        code, _out, err = run(["build", "--token", TOKEN, "--side", "sell", "--percent", "50", "--account", "0x12"])
        self.assertEqual(code, 1)
        self.assertIn("--account", err)


class PlanSubmitTests(unittest.TestCase):
    """`submit --plan` checks every signed transaction against the plan, then sends bundles or a broadcast."""

    def test_bundle_plan_is_sent_with_one_bundle_per_builder_and_the_trade_hashes_as_reverting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path, _plan = plan_file(tmp)
            bodies, code, out, err = submit(["--plan", str(path), "--signed", TIP_A["raw"], TIP_B["raw"], APPROVE["raw"], SELL["raw"]])
            self.assertEqual(code, 0, err)
            self.assertEqual(bodies, [{"bundles": [
                {"provider": "blockrazor", "txs": [TIP_A["raw"], APPROVE["raw"], SELL["raw"]], "revertingTxHashes": [APPROVE["hash"], SELL["hash"]]},
                {"provider": "48club", "txs": [TIP_B["raw"], APPROVE["raw"], SELL["raw"]], "revertingTxHashes": [APPROVE["hash"], SELL["hash"]]},
            ]}])
            result = json.loads(out)
            self.assertEqual((result["chain"], result["mode"], result["label"]), ("bsc", "bundle", "sell 12.5 TEST"))
            self.assertEqual([t["hash"] for t in result["transactions"]], [TIP_A["hash"], TIP_B["hash"], APPROVE["hash"], SELL["hash"]])
            self.assertEqual(result["watch"], [APPROVE["hash"], SELL["hash"]])
            self.assertEqual(result["results"][0]["provider"], "blockrazor")  # the service's answer is passed through

            signed_file = Path(tmp) / "signed.txt"
            signed_file.write_text("\n".join([TIP_A["raw"], TIP_B["raw"], APPROVE["raw"], SELL["raw"]]) + "\n", encoding="utf-8")
            bodies, code, _out, err = submit(["--plan", str(path), "--signed-file", str(signed_file)])
            self.assertEqual(code, 0, err)
            self.assertEqual(len(bodies[0]["bundles"]), 2)
            signed_file.write_text(json.dumps([TIP_A["raw"], TIP_B["raw"], APPROVE["raw"].upper().replace("0X", "0x"), SELL["raw"]]), encoding="utf-8")
            bodies, code, _out, err = submit(["--plan", str(path), "--signed-file", str(signed_file)])
            self.assertEqual(code, 0, err)
            self.assertEqual(bodies[0]["bundles"][0]["txs"][1], APPROVE["raw"])  # normalised to lower case

    def test_zero_tip_bundle_plan_sends_the_trade_alone_to_every_builder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path, plan = plan_file(tmp, nonce=18, extra=("--tip-wei", "0"))
            self.assertEqual([t["kind"] for t in plan["transactions"]], ["approve", "sell"])
            bodies, code, out, err = submit(["--plan", str(path), "--signed", APPROVE["raw"], SELL["raw"]])
            self.assertEqual(code, 0, err)
            self.assertEqual(bodies, [{"bundles": [
                {"provider": "blockrazor", "txs": [APPROVE["raw"], SELL["raw"]], "revertingTxHashes": [APPROVE["hash"], SELL["hash"]]},
                {"provider": "48club", "txs": [APPROVE["raw"], SELL["raw"]], "revertingTxHashes": [APPROVE["hash"], SELL["hash"]]},
            ]}])
            self.assertEqual(json.loads(out)["watch"], [APPROVE["hash"], SELL["hash"]])

    def test_broadcast_plan_is_sent_as_rpc_without_tips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path, plan = plan_file(tmp, side="buy", nonce=18, extra=("--broadcast",))
            self.assertEqual(plan["mode"], "rpc")
            bodies, code, out, err = submit(["--plan", str(path), "--signed", BUY["raw"]])
            self.assertEqual(code, 0, err)
            self.assertEqual(bodies, [{"rpc": {"txs": [BUY["raw"]]}}])
            self.assertEqual(json.loads(out)["watch"], [BUY["hash"]])

    def test_signed_transactions_that_do_not_match_the_plan_are_refused_before_any_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path, _plan = plan_file(tmp)
            cases = (
                ([TIP_B["raw"], TIP_A["raw"], APPROVE["raw"], SELL["raw"]], "transaction 0 (tip): to is"),
                ([TIP_A["raw"], TIP_B["raw"], SELL["raw"], APPROVE["raw"]], "transaction 2 (approve): nonce is 19"),
                ([TIP_A["raw"], TIP_B["raw"], APPROVE["raw"]], "4 transactions but 3"),
                ([TYPED, TIP_B["raw"], APPROVE["raw"], SELL["raw"]], "type 2"),
                ([TIP_A["raw"], TIP_B["raw"], APPROVE["raw"], "0xzz"], "not a signed transaction"),
            )
            for signed, message in cases:
                with self.subTest(message=message):
                    bodies, code, _out, err = submit(["--plan", str(path), "--signed", *signed])
                    self.assertEqual(code, 1)
                    self.assertIn(message, err)
                    self.assertEqual(bodies, [])
            path, _plan = plan_file(tmp, deadline="1763600000")
            bodies, code, _out, err = submit(["--plan", str(path), "--signed", TIP_A["raw"], TIP_B["raw"], APPROVE["raw"], SELL["raw"]])
            self.assertEqual(code, 1)
            self.assertIn("deadline has passed", err)
            self.assertEqual(bodies, [])
            bad = Path(tmp) / "bad.json"
            bad.write_text(json.dumps({"plan": {"chain": "bsc"}}), encoding="utf-8")
            _bodies, code, _out, err = submit(["--plan", str(bad), "--signed", TIP_A["raw"]])
            self.assertEqual(code, 1)
            self.assertIn("--plan", err)


class StreamAndSubmitTests(unittest.TestCase):
    def test_sse_parsing_and_watch_pending(self) -> None:
        events = list(dex.parse_sse(io.BytesIO(b"event: open\ndata: {\"a\":1}\n\nevent: receipt\ndata: {\"hash\":\"0xab\"}\n\n:comment\n\nevent: end\ndata: {}\n\n")))
        self.assertEqual(events, [("open", {"a": 1}), ("receipt", {"hash": "0xab"}), ("end", {})])
        h1, h2 = "0x" + "c1" * 32, "0x" + "c2" * 32
        fake = Fake({"/bsc/watch": sse([("open", {}), ("receipt", {"hash": h1, "status": 0}), ("timeout", {"hash": h2}), ("end", {"resolved": 1})])})
        with mock.patch.object(dex, "urlopen", fake):
            code, out, _err = run(["watch", "--hash", h1, h2, "--timeout", "2"])
        self.assertEqual(code, 0)
        result = json.loads(out)
        self.assertEqual(result["receipts"][0]["status"], 0)
        self.assertEqual(result["pending"], [h2])
        self.assertEqual(parse_qs(urlsplit(fake.request("/bsc/watch").full_url).query)["timeoutMs"], ["2000"])

    def test_submit_forwards_transactions_signed_elsewhere_and_bundles(self) -> None:
        bodies, code, out, err = submit(["--broadcast", TIP_A["raw"], SELL["raw"]])
        self.assertEqual(code, 0, err)
        self.assertEqual(bodies[-1], {"rpc": {"txs": [TIP_A["raw"], SELL["raw"]]}})
        self.assertEqual(json.loads(out)["hashes"][0], TIP_A["hash"])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bundles.json"
            path.write_text(json.dumps([{"provider": "blockrazor", "txs": [TIP_A["raw"]], "revertingTxHashes": []}]), encoding="utf-8")
            bodies, code, _out, err = submit(["--bundles", str(path)])
        self.assertEqual(code, 0, err)
        self.assertEqual(list(bodies[-1]), ["bundles"])
        for argv, message in (([], "--plan"), (["--broadcast", "0xzz"], "not a signed transaction"), (["--signed", TIP_A["raw"]], "--plan"), (["--broadcast", *([TIP_A["raw"]] * 17)], "1–16")):
            with self.subTest(argv=argv):
                bodies, code, _out, err = submit(argv)
                self.assertEqual(code, 1)
                self.assertIn(message, err)
                self.assertEqual(bodies, [])


class CredentialTests(unittest.TestCase):
    def test_env_token_wins_and_auth_never_prints_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code, out, _err = run(["auth"], {"NL_TRADE_HOME": tmp})
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"source": "NL_TRADE_TOKEN", "kind": "principal"})
        self.assertNotIn(SECRET, out)

    def test_principal_login_registers_signs_and_caches(self) -> None:
        try:
            import cryptography  # noqa: F401
        except ImportError:
            self.skipTest("cryptography not installed")
        import base64

        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

        issued = {}

        def token_route(request):
            body = json.loads(request.data)
            issued.update(body)
            key = dex.principal_private_key().public_key()
            sig = base64.urlsafe_b64decode(body["signature"] + "==")
            key.verify(encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big")), body["nonce"].encode(), ec.ECDSA(hashes.SHA256()))
            return {"token": "nlp_issued.token", "expiresAt": int((time.time() + 3600) * 1000), "principal": "p" * 32}

        fake = Fake({"/bsc/auth/challenge": {"nonce": "n0nce-value", "expiresAt": 1}, "/bsc/auth/token": token_route, "/bsc/health": {"ok": True}})
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"NL_TRADE_HOME": tmp}, clear=False):
            os.environ.pop("NL_TRADE_TOKEN", None)
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.object(dex, "urlopen", fake), mock.patch.object(dex, "open_session", lambda url: FakeSession(fake)), mock.patch.object(dex.sys, "stdout", out), mock.patch.object(dex.sys, "stderr", err):
                code = dex.main(["health"])
            self.assertEqual(code, 0, err.getvalue())
            self.assertEqual(issued["jwk"]["kty"], "EC")
            self.assertEqual(issued["jwk"]["crv"], "P-256")
            self.assertEqual(len(base64.urlsafe_b64decode(issued["jwk"]["x"] + "==")), 32)
            self.assertEqual(fake.request("/bsc/health").get_header("Authorization"), "Bearer nlp_issued.token")
            self.assertEqual(oct(os.stat(Path(tmp) / "token.json").st_mode & 0o777), "0o600")
            self.assertNotIn("nlp_issued", out.getvalue())
            challenges = len([r for r, _ in fake.requests if r.full_url.endswith("/auth/challenge")])
            with mock.patch.object(dex, "urlopen", fake), mock.patch.object(dex, "open_session", lambda url: FakeSession(fake)), mock.patch.object(dex.sys, "stdout", io.StringIO()):
                dex.main(["health"])
            self.assertEqual(len([r for r, _ in fake.requests if r.full_url.endswith("/auth/challenge")]), challenges)  # cached token reused

    def test_login_session_is_a_real_kept_alive_https_connection(self) -> None:
        session = dex.Session("https://example.invalid/v2/bsc/auth/challenge")
        self.assertIsInstance(session.connection, dex.httpclient.HTTPSConnection)
        self.assertEqual((session.connection.host, session.connection.port), ("example.invalid", 443))
        session.close()
        with self.assertRaises(dex.ApiError):
            dex.Session("http://example.invalid/x")

    def test_missing_cryptography_is_explained_not_crashed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"NL_TRADE_HOME": tmp}, clear=False), mock.patch.dict("sys.modules", {"cryptography": None}):
            os.environ.pop("NL_TRADE_TOKEN", None)
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.object(dex.sys, "stdout", out), mock.patch.object(dex.sys, "stderr", err):
                code = dex.main(["health"])
        self.assertEqual(code, 1)
        self.assertIn("NL_TRADE_TOKEN", err.getvalue())


if __name__ == "__main__":
    unittest.main()
