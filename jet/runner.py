"""Test script finder
Finds all scripts .py that start with test_ in the folder tests.
return all function name too?
"""
import os
import inspect
import importlib.util
import sys
import subprocess
import traceback
import warnings
import json
import io
from contextlib import redirect_stdout
import time
from functools import reduce
from operator import getitem
import re
import jet.checks as jetcheck

from rich.progress import Progress
from jet.mod_selection import choose_modules


warnings.filterwarnings("error")


class ErrorDuringImport(Exception):
    """Errors that occurred while trying to import something to document it."""

    def __init__(self, filename, exc_info):
        self.filename = filename
        self.exc, self.value, self.tb = exc_info

    def __str__(self):
        exc = self.exc.__name__
        return "problem in %s - %s: %s" % (self.filename, exc, self.value)


def _getitem(d, key):
    try:
        return reduce(getitem, key, d)
    except KeyError:
        return ""


def _importfile(path):
    """Import a Python source file or compiled file given its path."""
    magic = importlib.util.MAGIC_NUMBER
    with open(path, "rb") as file:
        is_bytecode = magic == file.read(len(magic))
    filename = os.path.basename(path)
    name, ext = os.path.splitext(filename)
    if is_bytecode:
        loader = importlib._bootstrap_external.SourcelessFileLoader(name, path)
    else:
        loader = importlib._bootstrap_external.SourceFileLoader(name, path)
    # XXXWe probably don't need to pass in the loader here.
    spec = importlib.util.spec_from_file_location(name, path, loader=loader)
    try:
        return importlib._bootstrap._load(spec)
    except:
        raise ErrorDuringImport(path, sys.exc_info())


def _clean_name(path):
    mod_name = os.path.split(path)[-1].removesuffix(".py").removeprefix("test_")
    mod_name = mod_name.split("_")
    mod_name = " ".join(mod_name)
    return mod_name.capitalize()


# path comes in -> module path, name, description and module
def _get_data(path):
    module = _importfile(path)
    name = _clean_name(path)
    data = {"doc": module.__doc__, "path": path, "module": module}
    return name, data


def _is_jsonable(x):
    try:
        json.dumps(x)
        return True
    except:
        return False


def _clean_variables(variables):
    if variables is None:
        return variables
    if len(variables) == 0:
        return variables

    new_variables = {}
    for k, v in variables.items():
        if hasattr(v, "__name__"):
            v = v.__name__
        elif not _is_jsonable(v):
            v = str(v)
        new_variables[k] = v
    return new_variables


def _catch(f, exc, info, variables):
    details = {
        "mod_path": info.filename,
        "line": info.lineno,
        "locals": _clean_variables(variables),
        "description": str(exc),
        "type": type(exc).__name__,
        "out": f.getvalue(),
    }
    return details


class Runner:
    def __init__(
        self,
        accent_color="134",  # 99 #38
        quiet=False,
        run_all=False,
        default_directory=None,
    ):
        self.modules = {}
        self.run_all = run_all
        self.quiet = quiet
        self.colors = {
            "Pass": "green",  # 92m
            "Failed": "red3",
            "Error": "orange3",
            "Warning": "yellow",
        }
        self.indentation = "    "
        self.accent_color = accent_color
        self.default_directory = default_directory
        if self.default_directory is None:
            self.default_directory = os.getcwd() + "/tests"

    def get_modules(self, path=None):
        if path is None:
            path = self.default_directory
        for dirpath, subdirs, files in os.walk(path):
            for x in files:
                if x.endswith(".py") and x.startswith("test_"):
                    name, data = _get_data(os.path.join(dirpath, x))
                    self.modules[name] = data

    def prompt_module_choice(self):
        options = [{"title": k, "desc": v["doc"]} for k, v in self.modules.items()]
        options.insert(0, {"title": "Run All", "desc": "Run all test modules found."})
        return choose_modules(options, color=self.accent_color)

    def fetch_modules(self):
        self.get_modules()
        if self.run_all:
            return
        choices = self.prompt_module_choice()
        if "Run All" in choices:
            return
        self.modules = {k: v for k, v in self.modules.items() if k in choices}
        return

    def fetch_module_routines(self, module, module_name):
        routines = []
        for key, value in inspect.getmembers(module, inspect.isroutine):
            if key.startswith("test"):
                routine_data = {
                    "routine": value,
                    "name": _clean_name(value.__name__),
                    "doc": value.__doc__ if value.__doc__ is not None else "",
                    "module": module_name,
                }
                routines.append(routine_data)
        return routines

    def fetch_tests(self):
        self.fetch_modules()
        tests = []
        for module_name in self.modules.keys():
            routines = self.fetch_module_routines(
                self.modules[module_name]["module"], module_name
            )
            tests.extend(routines)
        return tests

    def archive_routine_results(self, routine, result, details):
        test_result = {k: v for k, v in routine.items() if k != "routine"}
        test_result["result"] = result
        test_result["diagnosis"] = details
        self.results["tests"].append(test_result)

    def dump_results(self):
        cols = ["Name", "Result", "Diagnosis"]
        keys = [
            ["name"],
            ["result"],
            ["diagnosis", "description"],  # ["diagnosis", "ouput"]
        ]

        online_csv = ",".join(cols) + "\n"
        for test in self.results["tests"]:
            online_csv += (
                ",".join(
                    [re.sub("[^A-Za-z0-9_ ]+", "", _getitem(test, k)) for k in keys]
                )
                + "\n"
            )

        self.results["online"] = online_csv
        with open(self.default_directory + "/jet.results.json", "w") as fp:
            json.dump(self.results, fp)

    def do_jet_checks(self, routine):
        # add custom tests and checks here.
        result, details = jetcheck.arguments(routine)
        return result, details

    def evaluate(self, routine):
        result, details = self.do_jet_checks(routine)
        if result is not None:
            return result, details
        f = io.StringIO()
        try:
            with redirect_stdout(f):
                routine()
            return "Pass", None
        except AssertionError as exc:
            info = traceback.extract_tb(sys.exc_info()[2])[-1]
            variables = inspect.trace()[-1][0].f_locals
            details = _catch(f, exc, info, variables)
            return "Failed", details

        except RuntimeWarning as exc:
            info = traceback.extract_tb(sys.exc_info()[2])[-1]
            variables = inspect.trace()[-1][0].f_locals
            details = _catch(f, exc, info, variables)
            return "Warning", details

        except Exception as exc:
            info = traceback.extract_tb(sys.exc_info()[2])[-1]
            variables = inspect.trace()[-1][0].f_locals
            details = _catch(f, exc, info, variables)
            return "Error", details

    def run_tests(self):
        tests = self.fetch_tests()
        n_tests = len(tests)
        self.results = {
            "summary": {
                "n_tests": n_tests,
                "Pass": 0,
                "Failed": 0,
                "Warning": 0,
                "Error": 0,
            },
            "tests": [],
        }

        with Progress() as progress:
            task = progress.add_task("Running tests", total=n_tests)
            for routine in tests:
                # verbose with some extent
                result, details = self.evaluate(routine["routine"])
                self.results["summary"][result] += 1

                # archive only errors, warning and fails
                if result != "Pass":
                    self.archive_routine_results(routine, result, details)

                if not self.quiet:
                    progress.console.print(self.verbose_two(result, routine["routine"]))
                time.sleep(0.2)
                progress.advance(task)
            summary = self.verbose_one()
            self.results["summary"]["string"] = ""
            if summary != "Summary":
                progress.console.print(summary)
                self.results["summary"]["string"] = summary

        subprocess.run(["printf '\33[A[2K\r'"], shell=True)  # erase progress line
        self.dump_results()

    def verbose_one(self):
        s = "Summary: "
        for result in ["Pass", "Failed", "Warning", "Error"]:
            n = self.results["summary"][result]
            if n == 0:
                continue
            s += f"[{self.colors[result]}]{str(n)} {result.lower()}[/{self.colors[result]}], "
        return s[:-2]

    def verbose_two(self, result, routine):
        # TODO change color names to init
        doc = routine.__doc__
        if doc is None:
            doc = _clean_name(routine.__name__)
        if result == "Pass":
            tick = "\u2713"
            return f"[{self.colors['Pass']}]{tick}[/{self.colors['Pass']}] {doc}"
        if result == "Failed":
            cross = "\u2717"
            return f"[{self.colors['Failed']}]{cross}[/{self.colors['Failed']}] {doc}"
        else:
            mark = "?"
            return f"[{self.colors['Warning']}]{mark}[/{self.colors['Warning']}] {doc}"
