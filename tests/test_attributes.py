"""Every self.name a class reads must exist: a method, a class attribute, something
assigned to self in the class or a base, or an attribute of a library base class
(a GTK widget, say). Catches a renamed method with a reference left behind, which
otherwise only shows when that code runs: the tray's timer once named a method a
blanket rename had changed, and the tray crashed at every login."""
import ast
import importlib
import inspect
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

MODULES = sorted(f[:-3] for f in os.listdir(HERE) if f.endswith(".py"))
_parsed = {}


def class_nodes(name):
    """A module's classes, nested ones included, by qualified name."""
    if name not in _parsed:
        with open(os.path.join(HERE, name + ".py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        out = {}

        def walk(body, prefix):
            for node in body:
                if isinstance(node, ast.ClassDef):
                    out[prefix + node.name] = node
                    walk(node.body, prefix + node.name + ".")
        walk(tree.body, "")
        _parsed[name] = out
    return _parsed[name]


def self_attrs(cls_node):
    """(names the class body declares or assigns to self, [(name, line) read from self])."""
    defined, reads = set(), []
    for node in cls_node.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            defined.add(node.target.id)          # dataclass fields without a default
    stack = list(cls_node.body)
    while stack:                                 # nested classes have their own self
        node = stack.pop()
        stack.extend(c for c in ast.iter_child_nodes(node) if not isinstance(c, ast.ClassDef))
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self":
            if isinstance(node.ctx, ast.Store):
                defined.add(node.attr)
            elif isinstance(node.ctx, ast.Load):
                reads.append((node.attr, node.lineno))
    return defined, reads


def assigned_in(cls):
    """Names a class from this repo and its repo bases declare or assign to self."""
    names = set()
    for klass in inspect.getmro(cls):
        if klass.__module__ in MODULES:
            node = class_nodes(klass.__module__).get(klass.__qualname__)
            if node is not None:
                names |= self_attrs(node)[0]
    return names


def subclasses(cls):
    out, stack = [], list(cls.__subclasses__())
    while stack:
        sub = stack.pop()
        out.append(sub)
        stack.extend(sub.__subclasses__())
    return out


def problems_in(name):
    """'file:line: ...' for each self attribute read in module `name` that nothing defines.
    Imports the module (raises when it cannot), then every other module it can, so
    subclasses elsewhere are known."""
    module = importlib.import_module(name)
    for other in MODULES:
        try:
            importlib.import_module(other)
        except (ImportError, ValueError):
            pass
    problems = []
    for qualname, node in class_nodes(name).items():
        cls = module
        for part in qualname.split("."):
            cls = getattr(cls, part, None)
        if not inspect.isclass(cls):
            continue
        known = assigned_in(cls)
        for sub in subclasses(cls):       # a base may use a name only its subclasses provide
            known |= assigned_in(sub)
        for attr, line in self_attrs(node)[1]:
            if attr not in known and not hasattr(cls, attr):
                problems.append(f"{name}.py:{line}: {qualname} has no attribute {attr!r}")
    return problems


class AttributeTest(unittest.TestCase):
    def test_every_self_attribute_read_is_defined(self):
        for name in MODULES:
            if not class_nodes(name):
                continue
            with self.subTest(module=name):
                try:
                    problems = problems_in(name)
                except ValueError as e:
                    if "already loaded" not in str(e):
                        raise
                    # GTK 3 (tray) and GTK 4 (settings) cannot share a process
                    code = ("import sys; sys.path.insert(0, sys.argv[1]); import test_attributes as t; "
                            "print('\\n'.join(t.problems_in(sys.argv[2])))")
                    r = subprocess.run([sys.executable, "-c", code, os.path.dirname(__file__), name],
                                       capture_output=True, text=True, timeout=60)
                    if r.returncode < 0 and not os.environ.get("CI"):
                        # GTK aborted on start: a shell with another app's environment (a snap's)
                        self.skipTest(f"{name}: GTK crashed here: {r.stderr.strip()[-200:]}")
                    self.assertEqual(r.returncode, 0, r.stderr)
                    problems = r.stdout.strip().splitlines()
                except ImportError as e:
                    if os.environ.get("CI"):
                        raise
                    self.skipTest(f"{name}: {e}")    # GTK or AppIndicator missing on this machine
                self.assertEqual(problems, [], "\n" + "\n".join(problems))


if __name__ == "__main__":
    unittest.main()
