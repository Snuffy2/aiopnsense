System
==========

Interface toggling
------------------

``toggle_interface(if_name, toggle_on_off=None)`` requires OPNsense >= 26.7.6.
Use a logical identifier such as ``opt8`` and ``"on"``, ``"off"``, or ``None``
to enable, disable, or toggle its configured state. The method saves and applies
in one call, refusing existing or unreadable pending interface changes.
Apply affects the entire queue; coordinate updates with other clients.
Failed apply can leave a change queued. See the
`26.7.6 release notes <https://docs.opnsense.org/releases/CE_26.7.html#october-08-2026>`_
for legacy DHCP migration caveats.

.. opnsense-client-api:: aiopnsense.system.SystemMixin
