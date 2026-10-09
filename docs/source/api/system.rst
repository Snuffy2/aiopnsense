System
==========

Interface toggling
------------------

``toggle_interface(if_name, toggle_on_off=None)`` follows the existing toggle
methods: ``"on"`` enables an interface, ``"off"`` disables it, and ``None``
toggles its current configured state. Use logical identifiers such as ``wan``,
``lan``, or ``opt8`` rather than device names or interface descriptions.
OPNsense >= 26.7.6 is required; older or unknown firmware returns ``False``.

The method saves only the enable flag and applies the change in one call.
It returns ``True`` after successful apply, or when an explicit target already
matches the configured state. It returns ``False`` when the queue is already
pending or unreadable, the interface cannot be read, or save/apply fails.
An apply failure can leave the saved change queued on OPNsense.

OPNsense applies **all** queued interface changes. The method checks for an
empty queue before saving to avoid applying existing unrelated changes, but
this check is not an atomic reservation. Coordinate interface updates with
other users and clients. Disabling removes the configured addresses and
interrupts traffic using the interface. Link status can remain ``up`` while
an interface is disabled, so the method uses the configured enable flag.

The new API also migrates legacy interface settings on apply. In particular,
OPNsense documents that advanced/file-based DHCP modes reset on save. See the
`26.7.6 release notes <https://docs.opnsense.org/releases/CE_26.7.html#october-08-2026>`_
before using this method on interfaces with those settings.

Methods
-------

.. opnsense-client-api:: aiopnsense.system.SystemMixin
