# Vendored modules

This directory contains zero-dependency modules copied from
[Oaklight/zerodep](https://github.com/Oaklight/zerodep):

| Module | Version | License |
|---|---|---|
| `acp` | 0.3.5 | MIT |
| `jsonrpc` | 0.4.0 | MIT |

They are copied by the zerodep CLI and tracked in `zerodep.lock.json`.
Do not edit `acp/acp.py` or `jsonrpc/jsonrpc.py` by hand.

Update:

```bash
zerodep add acp -d src/agentabi/_vendor --nested
```

The package `__init__.py` files are local shims so the vendored modules can be
imported as `agentabi._vendor.acp` and `agentabi._vendor.jsonrpc`.
