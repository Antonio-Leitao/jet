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
from tqdm import tqdm
import time
import io
from contextlib import redirect_stdout


warnings.filterwarnings("error")


class ErrorDuringImport(Exception):
    """Errors that occurred while trying to import something to document it."""

    def __init__(self, filename, exc_info):
        self.filename = filename
        self.exc, self.value, self.tb = exc_info

    def __str__(self):
        exc = self.exc.__name__
        return "problem in %s - %s: %s" % (self.filename, exc, self.value)


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


def _catch(exc, f):
    """type, descriptiom, place"""
    out = f.getvalue()
    x = traceback.format_exc()
    details = {
        "type": type(exc).__name__,
        "description": str(exc),
        "place": [i.replace("  ", "") for i in x.split("\n") if i][-2],
        "output": out,
    }
    return details


# module comes in -> iterate through function


class Runner:
    def __init__(self):
        self.modules = {}
        self.verbose = 1
        self.colors = {
            "Pass": "\033[92m",
            "Failed": "\033[91m",
            "Warning": "\033[0m",
            "Error": "\033[93m",
            "End": "\033[0m",
        }

    def get_modules(self, path=None):
        if path is None:
            path = os.getcwd() + "/tests"
        for dirpath, subdirs, files in os.walk(path):
            for x in files:
                if x.endswith(".py") and x.startswith("test_"):
                    name, data = _get_data(os.path.join(dirpath, x))
                    self.modules[name] = data

    def choose_modules(self):
        self.get_modules()
        print("Which modules to run?")
        result = subprocess.run(
            ["gum", "choose", "--no-limit", "All"] + list(self.modules.keys()),
            stdout=subprocess.PIPE,
            text=True,
        )
        return result.stdout.splitlines()

    def fetch_modules(self):
        choices = self.choose_modules()
        if "All" in choices:
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

    def dump_results(self, path="result.json"):
        with open(path, "w") as fp:
            json.dump(self.results, fp)

    def evaluate(self, routine):
        f = io.StringIO()
        try:
            with redirect_stdout(f):
                routine()
            return "Pass", []
        except AssertionError as exc:
            return "Failed", _catch(exc, f)
        except RuntimeWarning as exc:
            return "Warning", _catch(exc, f)
        except Exception as exc:
            return "Error", _catch(exc, f)

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

        progress_bar = tqdm(
            tests,
            bar_format="{l_bar}{bar:50}| {n_fmt}/{total_fmt}",
            leave=False,
            position=0,
            colour="#E01563",
            postfix="",
        )

        with tqdm(
            total=n_tests, position=1, bar_format="{desc}", desc="All tests passed!"
        ) as desc:

            for routine in progress_bar:
                # verbose with some extent
                result, details = self.evaluate(routine["routine"])
                self.results["summary"][result] += 1

                # srchive only errors, warning and fails
                if result != "Pass":
                    self.archive_routine_results(routine, result, details)

                # VERBOSE
                # 1
                desc.set_description(self.verbose_one())
                # 2
                progress_bar.write(self.verbose_two(result, routine["routine"]))
                time.sleep(1)

        self.dump_results()

    def verbose_one(self):
        s = "Summary: "
        for result in ["Pass", "Failed", "Warning", "Error"]:
            n = self.results["summary"][result]
            if n == 0:
                continue
            s += f"{self.colors[result]}{str(n)} {result.lower()}{self.colors['End']}, "
        return s[:-2]

    def verbose_two(self, result, routine):

        doc = routine.__doc__
        if doc is None:
            doc = _clean_name(routine.__name__)

        if result == "Pass":
            tick = "\u2713"
            return f"{self.colors['Pass']}{tick} {self.colors['End']}{doc}"

        if result == "Failed":
            cross = "\u2717"
            return f"{self.colors['Failed']}{cross} {self.colors['End']}{doc}"

        else:
            mark = "?"
            return f"{self.colors['Error']}{mark} {self.colors['End']}{doc}"


if __name__ == "__main__":
    runner = Runner().run_tests()
