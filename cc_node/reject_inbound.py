#!/usr/bin/env python3
"""
Core Lightning plugin: bound inbound C&C peer channels at 2m (max_peers).

D-LNBot nodes stop advertising by closing their innocent channel after m inbound
peers (Algorithm 2). But a node hit by an inbound burst gets overloaded, its loop
falls behind, it never closes, and it balloons into a super-hub (we have seen
degree 38). This plugin runs inside lightningd, fires on every inbound open, and
rejects a new C&C-sized open once the node already holds 2m remote-opened C&C
channels -- a hard ceiling on the runaway, independent of whether cc_manager ever
manages to close.

The ceiling is 2m, not m, deliberately: nodes still close innocent at m, and the
gap between m (stop advertising) and 2m (hard reject) is the overflow capacity
formation needs to converge -- capping at exactly m leaves zero slack and stalls
the last nodes. So most nodes settle near m inbound (paper-like), while the few
overloaded ones are bounded at 2m instead of running away.

Only C&C-sized opens are gated. The botmaster's injection channel (~0.12 BTC) is
far larger than a C&C channel (<=0.0015 BTC), so it is always allowed and command
injection keeps working. Loaded only in formation mode (see node_start.sh), and
fails open (allows the open) on any error, so it can never wedge channel setup.
"""
import json
from pathlib import Path

from pyln.client import Plugin

plugin = Plugin()


def _load_params():
    """The inbound ceiling (max_peers = 2m) and the C&C channel-size band, read
    from the same node_config.json that ln_checker uses. Safe defaults if it
    can't be read.

    The ceiling is 2m, NOT m, on purpose: nodes still close their innocent
    channel at m inbound (Algorithm 2), and formation needs overflow capacity
    above m to converge (capping at exactly m leaves zero slack and stalls the
    last nodes). 2m bounds runaway hubs while preserving that slack."""
    cap, lo_sat, hi_sat = 8, 50000, 150000
    try:
        cfg = Path(__file__).resolve().parent / "testState" / "node_config.json"
        if cfg.exists():
            d = json.load(open(cfg))
            cap = int(d.get("max_peers", cap))
            lo_sat = int(d.get("min_channel_capacity", lo_sat))
            hi_sat = int(d.get("max_channel_capacity", hi_sat))
    except Exception:
        pass
    return cap, lo_sat * 1000, hi_sat * 1000  # band in msat


MAX_INBOUND, CC_MIN_MSAT, CC_MAX_MSAT = _load_params()


def _to_msat(v):
    try:
        return int(str(v).lower().replace("msat", "").strip())
    except Exception:
        return -1


def _is_cc_amount(msat):
    return CC_MIN_MSAT <= msat <= CC_MAX_MSAT


def _incoming_msat(payload):
    for k in ("funding_msat", "their_funding_msat"):
        if k in payload:
            return _to_msat(payload[k])
    if "funding_satoshis" in payload:           # older CLN: value in sat
        s = _to_msat(payload["funding_satoshis"])
        return s * 1000 if s >= 0 else -1
    return -1


def _inbound_cc_count():
    """How many remote-opened, C&C-sized channels this node already has."""
    try:
        chans = plugin.rpc.listpeerchannels().get("channels", [])
    except Exception:
        return 0  # fail open: don't reject if we can't count
    n = 0
    for c in chans:
        if c.get("opener") != "remote":
            continue
        if _is_cc_amount(_to_msat(c.get("total_msat", c.get("amount_msat", 0)))):
            n += 1
    return n


def _decide(payload):
    amt = _incoming_msat(payload)
    # Gate only C&C-sized opens; always allow the large botmaster channel (or an
    # amount we couldn't parse).
    if amt < 0 or not _is_cc_amount(amt):
        return {"result": "continue"}
    if _inbound_cc_count() >= MAX_INBOUND:
        return {"result": "reject", "error_message": "node is full (max inbound C&C peers reached)"}
    return {"result": "continue"}


@plugin.hook("openchannel")
def on_openchannel(plugin, **kwargs):
    return _decide(kwargs.get("openchannel") or {})


@plugin.hook("openchannel2")
def on_openchannel2(plugin, **kwargs):
    return _decide(kwargs.get("openchannel2") or {})


plugin.run()
