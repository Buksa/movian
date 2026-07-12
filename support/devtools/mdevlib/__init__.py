"""Movian dev/test harness package (mdev CLI + ported agent scripts).

Python 3 stdlib only.  State lives in /tmp/mdev/<instance>/, never in the
repository.  Movian must always be launched from the repo root because the
debug build resolves dataroot:// against the current working directory.
"""
