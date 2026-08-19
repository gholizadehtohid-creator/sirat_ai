"""Test package for SIRAT-AI.

WHY THIS FILE EXISTS
--------------------
Without it `tests` is a namespace package, and `unittest.discover(start_dir,
top_level_dir=ROOT)` refuses a start directory it cannot import -- which is why
the suite previously had to be run as `python -m unittest discover -s tests`
with no top-level directory, importing each file as a top-level module.

That worked, and it had one real cost: a test that imported a helper from a
sibling (`from tests.test_experiment_gates import panel`) got a SECOND copy of
that module rather than the one already loaded. Two copies of a module with
module-level state is a class of test bug that is very hard to see and very easy
to write.

With this file, `ci.py` discovers the suite from the project root, every test
module is imported once under one name, and both invocations still work:

    python ci.py
    python -m unittest discover -s tests -t .
"""
