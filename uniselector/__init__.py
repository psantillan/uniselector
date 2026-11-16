"""Uniselector: an Ashby regulator for long-horizon agent work, built on DataHandles.

A main agent builds objects on DataHandles handles and states the essential variables: sentences
that are true while it can sleep. The objects run with no model. A decision model judges only if
each variable is inside its limits. When one is not, the runner of the object that is broken gets
a wake and replaces that object.
"""
