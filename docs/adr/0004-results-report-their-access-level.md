# Results report their access level on the result object, detected from the Router

An API user may have Status Access to a category, or to one VPN type, without Configuration Access. A forbidden (403) configuration read then means "this user may not see the configured inventory", not "nothing is configured". A caller such as hass-opnsense, which deletes entities missing from an inventory it believes complete, must be able to tell the two apart. aiopnsense therefore returns the data that Status Access allows and reports the access level with it: a getter that supports access reporting returns a `dict` or `list` subclass carrying the level (Status Access or Configuration Access), read through an exported `result_access()` helper. The level is detected from the Router's answers, never set by the caller. A 403 on a configuration endpoint means Status Access; a 404 still means the endpoint is missing and its inventory is empty; a 403 or other failure on the status endpoint itself still returns `None`, with no level.

The first getters to report are `get_openvpn`, `get_wireguard` and `get_ipsec`. Other categories adopt the same shape when they gain Status Access support.

## Considered Options

- **A top-level `access` key in the result mapping.** Rejected: additive for mapping results, but a category whose getter returns a list could only adopt it by wrapping its result, a breaking change for each such category.
- **A separate client call reporting the last access level.** Rejected: the level is not tied to the data it describes, so an interleaved call to the same getter can make them disagree, and every caller must remember to ask immediately after each read.
- **Returning `None` for a forbidden configuration read.** Rejected: safe against deletion, but it discards the status data a Status Access user is entitled to and leaves a downstream inventory permanently incomplete.
- **A user-chosen access level in the downstream sync options.** Rejected: it can disagree with the Router's actual permissions, and choosing a level the Router does not grant has no sensible meaning.

## Consequences

- Results still compare equal to, and pass `isinstance` checks as, plain `dict` and `list` values, so existing callers and tests are unaffected. Declared return types stay unchanged.
- The level survives `copy.deepcopy` and pickling, but a plain copy (`dict(x)`, `{**x}`, `list(x)`, JSON) silently drops it. `result_access()` then returns `None`, meaning "not reported", which callers must treat as the behavior from before access reporting existed.
- Under Status Access, items the status does not show, such as disabled VPN Instances, are absent, and nothing can be Toggled.
- The WireGuard status endpoint exposes no UUIDs, so every WireGuard Server and WireGuard Client entry carries a `key_hash` derived from its public key, at both access levels. Under Status Access, entries are keyed by `key_hash` with no UUID; under Configuration Access, they stay keyed by UUID. Callers that need one identity across both levels use `key_hash`, so its derivation is part of the public contract: changing it changes every identity built from it.
