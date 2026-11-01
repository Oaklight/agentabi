# Vendored modules

This directory contains zero-dependency modules copied from
[Oaklight/zerodep](https://github.com/Oaklight/zerodep):

| Module | Version | License |
|---|---|---|
| `acp` | 0.3.6 | MIT |
| `jsonrpc` | 0.4.0 | MIT |
| `ptyx` | 0.1.0 | MIT |

`acp` 0.3.6 comes from `Oaklight/zerodep@f525a9d` on `master`; it is not in a
tagged release yet (the newest tag carries 0.3.2). Re-vendor from that commit
or later until zerodep tags a release, otherwise `zerodep add acp` downgrades.
`ptyx` 0.1.0 comes from `Oaklight/zerodep@918980c` on `master` and is likewise
untagged.

They are copied by the zerodep CLI and tracked in `zerodep.lock.json`.
Do not edit `acp/acp.py`, `jsonrpc/jsonrpc.py`, or `ptyx/ptyx.py` by hand.

Update:

```bash
zerodep --ref master add acp ptyx -d src/agentabi/_vendor --nested --force
```

The package `__init__.py` files are local shims so the vendored modules can be
imported as `agentabi._vendor.acp`, `agentabi._vendor.jsonrpc`, and
`agentabi._vendor.ptyx`.
