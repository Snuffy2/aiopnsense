Requirements
==============

.. admonition:: Note
   :class: note

   Requires OPNsense Firmware >= 25.1

- Recommended OPNsense Firmware >= 26.1.1

  - For firmware < 26.1.1, the Firewall and NAT methods will return empty data.

- ``toggle_interface()`` requires firmware >= 26.7.6 and returns ``False``
  without changing interfaces on older or unknown firmware.
