#!/usr/bin/env python
"""Minimal test runner for environments where pytest cannot create temp dirs.

pytest's ``tmp_path`` fixture needs a writable system temp directory, which is
not available inside some sandboxes.  This runner imports the test modules
directly, injects an equivalent ``tmp_path`` argument backed by a directory we
create ourselves, and reports pass/fail per test.

    python tools/run_tests.py [--keep] [--verbose]
"""
from __future__ import annotations

import argparse
import contextlib
import importlib
import importlib.util
import inspect
import io
import os
import shutil
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"

sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(TESTS))


class _MonkeyPatch:
    """Just enough of pytest's monkeypatch for our tests."""

    def __init__(self) -> None:
        self._undo = []

    def setattr(self, target: str, value) -> None:
        module_name, _, attribute = target.rpartition(".")
        # Walk outwards until we find the module that actually holds the name,
        # so "pkg.mod.attr" and "pkg.mod.submodule.attr" both work.
        while module_name:
            if module_name in sys.modules:
                break
            try:
                importlib.import_module(module_name)
                break
            except ImportError:
                module_name, _, extra = module_name.rpartition(".")
                attribute = f"{extra}.{attribute}"
        module = sys.modules[module_name]
        self._undo.append((module, attribute, getattr(module, attribute)))
        setattr(module, attribute, value)

    def chdir(self, path) -> None:
        self._undo.append((None, None, os.getcwd()))
        os.chdir(path)

    def undo(self) -> None:
        for module, attribute, original in reversed(self._undo):
            if module is None:
                os.chdir(original)
            else:
                setattr(module, attribute, original)


class _CapSys:
    """Minimal stand-in for pytest's ``capsys`` fixture."""

    def __init__(self, stdout_buffer, stderr_buffer):
        self._stdout = stdout_buffer
        self._stderr = stderr_buffer

    def readouterr(self):
        import collections

        out = self._stdout.getvalue()
        err = self._stderr.getvalue()
        self._stdout.seek(0)
        self._stdout.truncate(0)
        self._stderr.seek(0)
        self._stderr.truncate(0)
        return collections.namedtuple("CaptureResult", "out err")(out, err)


def _expect_raises(expected, func, *args, **kwargs) -> bool:
    try:
        func(*args, **kwargs)
    except expected:
        return True
    except Exception as exc:  # wrong exception type
        raise AssertionError(
            f"expected {expected.__name__}, got {type(exc).__name__}: {exc}"
        ) from exc
    raise AssertionError(f"expected {expected.__name__}, nothing was raised")


class _Raises:
    """Context manager mirroring ``pytest.raises``."""

    def __init__(self, expected, match=None):
        self.expected = expected
        self.match = match
        self.value = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            raise AssertionError(f"expected {self.expected.__name__}, nothing raised")
        if not issubclass(exc_type, self.expected):
            return False
        self.value = exc
        if self.match is not None:
            import re
            if not re.search(self.match, str(exc)):
                raise AssertionError(
                    f"{exc!r} does not match {self.match!r}"
                )
        return True


def _install_stubs() -> None:
    """Provide the handful of pytest helpers the tests use."""
    if "pytest" in sys.modules:
        return

    import types

    stub = types.ModuleType("pytest")
    stub._parametrize_registry = {}

    def raises(expected, match=None):
        return _Raises(expected, match)

    def importorskip(name, *args, **kwargs):
        try:
            return importlib.import_module(name)
        except ImportError:
            raise SkipTest(f"{name} not installed")

    def fixture(*args, **kwargs):
        def decorate(func):
            return func
        if args and callable(args[0]):
            return args[0]
        return decorate

    def parametrize(argnames, argvalues, **kwargs):
        names = [n.strip() for n in argnames.split(",")] if isinstance(argnames, str) else list(argnames)

        def decorate(func):
            stub._parametrize_registry.setdefault(func, []).append((names, list(argvalues)))
            return func
        return decorate

    stub.raises = raises
    stub.importorskip = importorskip
    stub.fixture = fixture
    stub.parametrize = parametrize
    stub.skip = lambda reason="": (_ for _ in ()).throw(SkipTest(reason))
    stub.mark = types.SimpleNamespace(parametrize=parametrize)
    sys.modules["pytest"] = stub


def _cases_for(func):
    """Expand a test function into (case_id, kwargs) pairs, honouring parametrize."""
    stub = sys.modules.get("pytest")
    registry = getattr(stub, "_parametrize_registry", {}) if stub else {}
    sets = registry.get(func)
    if not sets:
        return [("", {})]

    cases = [("", {})]
    for names, values in sets:
        expanded = []
        for base_id, base_kwargs in cases:
            for index, value in enumerate(values):
                if len(names) == 1:
                    items = {names[0]: value}
                    suffix = f"[{_case_label(value, index)}]"
                else:
                    items = dict(zip(names, value))
                    suffix = f"[{_case_label(value, index)}]"
                merged = dict(base_kwargs)
                merged.update(items)
                expanded.append((base_id + suffix, merged))
        cases = expanded
    return cases


def _case_label(value, index):
    if isinstance(value, (tuple, list)):
        return "-".join(str(v) for v in value)
    if isinstance(value, bytes):
        return f"bytes{index}"
    return str(value)


class SkipTest(Exception):
    """Raised to skip a test."""


def _iter_tests(module):
    """Yield (name, func) for every ``test_*`` callable in ``module``."""
    for name in sorted(vars(module)):
        if not name.startswith("test_"):
            continue
        func = getattr(module, name)
        if callable(func):
            yield name, func


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true", help="keep temp directories")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--only", help="substring filter on test names")
    args = parser.parse_args()

    _install_stubs()

    workdir = ROOT / "_testtmp"
    if workdir.exists():
        shutil.rmtree(workdir, ignore_errors=True)
    workdir.mkdir(parents=True, exist_ok=True)

    passed = failed = skipped = 0
    failures = []

    for path in sorted(TESTS.glob("test_*.py")):
        module_name = path.stem
        spec = importlib.util.spec_from_file_location(module_name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            print(f"[import error] {module_name}")
            traceback.print_exc()
            failed += 1
            continue

        tests = list(_iter_tests(module))
        for name, func in tests:
            for case_id, case_kwargs in _cases_for(func):
                label = f"{module_name}::{name}{case_id}"
                if args.only and args.only not in label:
                    continue

                signature = inspect.signature(func)
                tmpdir = workdir / f"{module_name}-{name}{case_id}".replace("[", "_").replace("]", "")
                tmpdir.mkdir(parents=True, exist_ok=True)

                kwargs = dict(case_kwargs)
                needs_patch = "monkeypatch" in signature.parameters
                needs_capsys = "capsys" in signature.parameters
                patch = _MonkeyPatch() if needs_patch else None
                out_buffer = io.StringIO() if needs_capsys else None
                err_buffer = io.StringIO() if needs_capsys else None

                try:
                    if "tmp_path" in signature.parameters:
                        kwargs["tmp_path"] = tmpdir
                    if needs_patch:
                        kwargs["monkeypatch"] = patch
                    if needs_capsys:
                        kwargs["capsys"] = _CapSys(out_buffer, err_buffer)
                        with contextlib.redirect_stdout(out_buffer), \
                                contextlib.redirect_stderr(err_buffer):
                            func(**kwargs)
                    else:
                        func(**kwargs)
                    passed += 1
                    if args.verbose:
                        print(f"PASS {label}")
                except SkipTest as exc:
                    skipped += 1
                    print(f"SKIP {label}: {exc}")
                except Exception as exc:
                    failed += 1
                    failures.append((label, exc))
                    print(f"FAIL {label}: {type(exc).__name__}: {exc}")
                    if args.verbose:
                        traceback.print_exc()
                finally:
                    if patch is not None:
                        patch.undo()

    if not args.keep:
        shutil.rmtree(workdir, ignore_errors=True)

    print()
    print(f"{passed} passed, {failed} failed, {skipped} skipped")
    if failures and not args.verbose:
        print("\nfailures:")
        for label, exc in failures:
            print(f"  {label}: {type(exc).__name__}: {exc}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
