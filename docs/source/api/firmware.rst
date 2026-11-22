Firmware
============

Firmware requirements
---------------------

``get_firmware_requirements()`` reports the Minimum Firmware and Recommended
Firmware that aiopnsense supports. It reads library constants and does not
contact the Router, so it can be called before a client exists.

.. autofunction:: aiopnsense.get_firmware_requirements

.. autoclass:: aiopnsense.FirmwareRequirements

Client methods
--------------

.. opnsense-client-api:: aiopnsense.firmware.FirmwareMixin
