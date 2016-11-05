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
