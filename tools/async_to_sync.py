#!/usr/bin/env python
"""Convert an async module to a sync module.
"""

from __future__ import annotations

import os
import sys
from argparse import ArgumentParser, Namespace

import ast_comments as ast


def main() -> int:
    opt = parse_cmdline()
    with open(opt.filename) as f:
        source = f.read()

    tree = ast.parse(source, filename=opt.filename)
    tree = async_to_sync(tree)
    output = tree_to_str(tree, opt.filename)

    if opt.output:
        with open(opt.output, "w") as f:
            print(output, file=f)
    else:
        print(output)

    return 0


def async_to_sync(tree: ast.AST) -> ast.AST:
    tree = BlanksInserter().visit(tree)
    tree = AsyncToSync().visit(tree)
    tree = RenameAsyncToSync().visit(tree)
    tree = FixAsyncSetters().visit(tree)
    return tree


def tree_to_str(tree: ast.AST, filename: str) -> str:
    rv = f"""\
# WARNING: this file is auto-generated by '{os.path.basename(sys.argv[0])}'
# from the original file '{os.path.basename(filename)}'
# DO NOT CHANGE! Change the original file instead.
"""
    rv += ast.unparse(tree)
    return rv


class AsyncToSync(ast.NodeTransformer):
    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
        new_node = ast.FunctionDef(
            name=node.name,
            args=node.args,
            body=node.body,
            decorator_list=node.decorator_list,
            returns=node.returns,
        )
        ast.copy_location(new_node, node)
        self.visit(new_node)
        return new_node

    def visit_AsyncFor(self, node: ast.AsyncFor) -> ast.AST:
        new_node = ast.For(
            target=node.target, iter=node.iter, body=node.body, orelse=node.orelse
        )
        ast.copy_location(new_node, node)
        self.visit(new_node)
        return new_node

    def visit_AsyncWith(self, node: ast.AsyncWith) -> ast.AST:
        new_node = ast.With(items=node.items, body=node.body)
        ast.copy_location(new_node, node)
        self.visit(new_node)
        return new_node

    def visit_Await(self, node: ast.Await) -> ast.AST:
        new_node = node.value
        self.visit(new_node)
        return new_node


class RenameAsyncToSync(ast.NodeTransformer):
    names_map = {
        "AsyncClientCursor": "ClientCursor",
        "AsyncCursor": "Cursor",
        "AsyncRawCursor": "RawCursor",
        "AsyncServerCursor": "ServerCursor",
        "aclose": "close",
        "aclosing": "closing",
        "aconn": "conn",
        "aconn_cls": "conn_cls",
        "aconn_set": "conn_set",
        "alist": "list",
        "anext": "next",
    }

    def visit_Module(self, node: ast.Module) -> ast.AST:
        # Replace the content of the module docstring.
        if (
            node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
        ):
            node.body[0].value.value = node.body[0].value.value.replace("Async", "")

        self.generic_visit(node)
        return node

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        node.name = self.names_map.get(node.name, node.name)
        for arg in node.args.args:
            arg.arg = self.names_map.get(arg.arg, arg.arg)
        self.generic_visit(node)
        return node

    _skip_imports = {"alist", "anext"}

    def visit_ImportFrom(self, node: ast.ImportFrom) -> ast.AST | None:
        # Remove import of async utils eclypsing builtins
        if node.module == "utils":
            node.names = [n for n in node.names if n.name not in self._skip_imports]
            if not node.names:
                return None

        for n in node.names:
            n.name = self.names_map.get(n.name, n.name)
        return node

    def visit_Name(self, node: ast.Name) -> ast.AST:
        if node.id in self.names_map:
            node.id = self.names_map[node.id]
        return node

    def visit_Attribute(self, node: ast.Attribute) -> ast.AST:
        if node.attr in self.names_map:
            node.attr = self.names_map[node.attr]
        self.generic_visit(node)
        return node


class FixAsyncSetters(ast.NodeTransformer):
    setters_map = {
        "set_autocommit": "autocommit",
        "set_read_only": "read_only",
        "set_isolation_level": "isolation_level",
        "set_deferrable": "deferrable",
    }

    def visit_Call(self, node: ast.Call) -> ast.AST:
        new_node = self._fix_setter(node)
        if new_node:
            return new_node

        self.generic_visit(node)
        return node

    def _fix_setter(self, node: ast.Call) -> ast.AST | None:
        if not isinstance(node.func, ast.Attribute):
            return None
        if node.func.attr not in self.setters_map:
            return None
        obj = node.func.value
        arg = node.args[0]
        new_node = ast.Assign(
            targets=[ast.Attribute(value=obj, attr=self.setters_map[node.func.attr])],
            value=arg,
        )
        ast.copy_location(new_node, node)
        return new_node


class BlanksInserter(ast.NodeTransformer):
    """
    Restore the missing spaces in the source (or something similar)
    """

    def generic_visit(self, node: ast.AST) -> ast.AST:
        if isinstance(getattr(node, "body", None), list):
            node.body = self._inject_blanks(node.body)
        super().generic_visit(node)
        return node

    def _inject_blanks(self, body: list[ast.Node]) -> list[ast.AST]:
        if not body:
            return body

        new_body = []
        before = body[0]
        new_body.append(before)
        for i in range(1, len(body)):
            after = body[i]
            nblanks = after.lineno - before.end_lineno - 1
            if nblanks > 0:
                # Inserting one blank is enough.
                blank = ast.Comment(
                    value="",
                    inline=False,
                    lineno=before.end_lineno + 1,
                    end_lineno=before.end_lineno + 1,
                    col_offset=0,
                    end_col_offset=0,
                )
                new_body.append(blank)
            new_body.append(after)
            before = after

        return new_body


def parse_cmdline() -> Namespace:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument(
        "filename",
        metavar="FILE",
        help="the file to process",
    )
    parser.add_argument(
        "output", metavar="OUTPUT", nargs="?", help="file where to write (or std0ut)"
    )
    opt = parser.parse_args()

    return opt


if __name__ == "__main__":
    sys.exit(main())
