"""Uniselector: an Ashby regulator for long-horizon agent work, built on DataHandles.

A main agent hands off a plan: a list of intents. A decision model carries out each intent by
choosing operations on DataHandles handles. The main agent sees nothing until the work is done,
an action needs its confirmation, or the decision model gives up.
"""
