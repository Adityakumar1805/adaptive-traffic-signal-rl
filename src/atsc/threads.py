"""Thread-pool settings for the long-running dashboard process.

``limit_math_threads()`` must run before NumPy (or PyTorch) is imported: BLAS and OpenMP
read these variables once, when the library loads. This module therefore imports nothing
heavy, and neither does the ``atsc`` package itself.
"""
from __future__ import annotations

import os

#: Thread-pool variables pinned to one thread for the dashboard: OpenBLAS (pip NumPy),
#: OpenMP (PyTorch, MKL builds) and MKL (conda NumPy).
MATH_THREAD_VARS = ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS")


def limit_math_threads() -> None:
    """Run NumPy's BLAS (and PyTorch's OpenMP pool) on one thread, unless the environment
    already chose a value.

    The dashboard evaluates a 23-input network for four junctions five times a second.
    Matrices that small gain nothing from extra threads, and OpenBLAS's idle workers
    spin-wait after every call: with the default pool the hosted server used about half a
    CPU while doing nothing (about 47 % measured, under 2 % with one thread), which on a
    free instance with 0.1 CPU starves the event loop that sends the frames. Training and
    the benchmark do not call this, so they run exactly as before.
    """
    for var in MATH_THREAD_VARS:
        os.environ.setdefault(var, "1")
