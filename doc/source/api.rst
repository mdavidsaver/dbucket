API Reference
=============

Value encode/decode
===================

.. py:module:: dbucket.xcode

.. autofunction:: encode
.. autofunction:: decode

.. autoclass:: Variant
.. autoclass:: Object
.. autoclass:: Signature

Signal reception
================

.. py:module:: dbucket.signal

.. autoclass:: Condition
.. autoclass:: SignalQueue
   :members: NORMAL, OFLOW, DONE, add, remove, recv, poll, close

Bus connecting/authentication
=============================

.. py:module:: dbucket.auth

.. autofunction:: connect_bus
.. autofunction:: get_session_infos
.. autofunction:: get_system_infos

Bus Connection
==============

.. py:module:: dbucket.conn

.. autodata:: DBUS
.. autodata:: DBUS_PATH
.. autodata:: INTROSPECTABLE

.. autoclass:: ConnectionClosed
.. autoclass:: RemoteError
.. autoclass:: NoReplyError
.. autoclass:: BusEvent
   :members:

.. autoclass:: Connection

   .. autoattribute:: loop
   .. autoattribute:: name
   .. autoattribute:: names
   .. autoattribute:: running
   .. automethod:: close
   .. automethod:: call
   .. automethod:: signal
