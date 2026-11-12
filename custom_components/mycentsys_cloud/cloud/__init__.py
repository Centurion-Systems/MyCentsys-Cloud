"""MyCentsys Cloud protocol package.

Everything in here is written from Centurion Systems' own device and backend
sources. It has no Home Assistant dependency, so it can later move into a
standalone library.

Modules:

* ``regions``   datacentre hosts and the broker identity
* ``numbers``   phone number normalisation
* ``errors``    the exceptions every caller can expect
* ``models``    typed views of what the backends return
* ``http``      the HTTP client: sign-in, listings, module control, sharing
* ``wire``      secure remote-activation packets exchanged with a SMART operator
* ``actions``   activation identifiers per operator family
* ``products``  product codes and operator families
* ``overview``  decoding of the status telemetry an operator publishes
* ``mqtt``      the broker session: connect, authenticate, activate, listen
"""
