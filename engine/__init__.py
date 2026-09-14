"""cellengine.engine: framework-free single-cell pipeline.

Nothing in this package imports Django, Redis, or RQ. It takes an AnnData in and
returns numpy/scipy objects out, so it can be tested and profiled on its own.
"""
