# A Toggle includes applying the change, and a matching VPN Toggle skips the apply

OPNsense separates saving a change from applying it: the toggle endpoint only saves the new enabled state, and a separate apply or reconfigure call makes it take effect. aiopnsense hides that split. Every Toggle (Firewall Rule, NAT rule, alias, interface, VPN Instance) saves and applies in one method, and the library exposes no standalone apply for callers such as hass-opnsense, which only ever need the item switched.

For a VPN Instance given an explicit target state, a request that already matches the saved state returns success without reconfiguring the VPN service. Reconfiguring on every no-op restarts the service and can drop live tunnels, so it is avoided. The one exception is a change this client saved but failed to apply: a later matching request retries that apply, so a save is not left unapplied.

## Considered Options

- **Expose apply as a separate public method.** Rejected: it would make every caller remember a second step, and a forgotten apply leaves the Router showing a state it is not running.
- **Always apply after every Toggle, including no-ops.** Rejected for VPN Instances: it disrupts a running VPN service when nothing changed. The other Toggles still apply after every successful save.

## Consequences

- Cancelling a Toggle while a POST is still queued skips that POST and sends no subsequent steps. Earlier writes may already have succeeded. Once a non-VPN save is dispatched, it can complete after caller cancellation and leave a change awaiting apply.
- Pending unapplied saves are tracked in memory on one client. Another client, an external writer or a restart does not know about them, so a matching request from there skips the apply.
- Closing the client can cancel a dispatched VPN save before OPNsense answers. OPNsense may have saved the change while the client does not track it, and a later matching request can then skip the apply. Shutdown stays prompt rather than waiting for that response.
- WireGuard Toggles read the current state first, because released firmware through 26.7.9 ignores the explicit target in its toggle path and flips instead.
