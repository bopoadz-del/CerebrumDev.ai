"""Domain action packages.

Each subpackage ``app.retailops.kit.domains.<kit>`` may expose an ``actions`` module with an
``ACTION_SPECS`` list that the generic :class:`~app.retailops.kit.contract.ActionRegistry`
discovers. Adding a new domain kit here requires no change to the registry or
any host dispatcher.
"""
