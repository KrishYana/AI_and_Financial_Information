from __future__ import annotations

import importlib
import sys
import types
from typing import Any


def install_textual_stub() -> None:
    if "textual" in sys.modules:
        return

    textual_module = types.ModuleType("textual")
    app_module = types.ModuleType("textual.app")
    screen_module = types.ModuleType("textual.screen")
    containers_module = types.ModuleType("textual.containers")
    widgets_module = types.ModuleType("textual.widgets")

    class Widget:
        def __init__(self, *children: Any, id: str | None = None, classes: str | None = None, **kwargs: Any):
            _ = kwargs
            self.children = list(children)
            self.id = id
            self._classes = set((classes or "").split()) if classes else set()

        def add_class(self, name: str) -> None:
            self._classes.add(name)

        def remove_class(self, name: str) -> None:
            self._classes.discard(name)

        def has_class(self, name: str) -> bool:
            return name in self._classes

        def set_interval(self, *_args: Any, **_kwargs: Any) -> None:
            return None

        def refresh(self) -> None:
            return None

    class _QueryResult(list):
        def first(self):
            return self[0]

    class App:
        CSS_PATH: str | None = None
        BINDINGS: list[tuple[str, str, str]] = []

        def run(self) -> None:
            return None

        def query_one(self, selector: str, widget_type=None):
            raise LookupError(f"No widget registered for selector: {selector}")

        def push_screen(self, _screen):
            return None

        def pop_screen(self):
            return None

        def query(self, _selector: str):
            return _QueryResult()

    def on(_event_type, _selector=None):
        def decorator(func):
            return func

        return decorator

    class _Container(Widget):
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            _ = (exc_type, exc, tb)
            return False

    class Horizontal(_Container):
        pass

    class Vertical(_Container):
        pass

    class VerticalScroll(_Container):
        pass

    class Header(Widget):
        pass

    class Footer(Widget):
        pass

    class Screen(Widget):
        def query_one(self, selector: str, widget_type=None):
            raise LookupError(f"No widget registered for selector: {selector}")

        def query(self, _selector: str):
            return _QueryResult()

        def run_worker(self, _coro, **_kwargs):
            return None

    class DirectoryTree(Widget):
        class FileSelected:
            def __init__(self, path):
                self.path = path

        def __init__(self, path: str, *args: Any, **kwargs: Any):
            super().__init__(*args, **kwargs)
            self.path = path

    class Label(Widget):
        def __init__(self, text: str = "", *args: Any, **kwargs: Any):
            super().__init__(*args, **kwargs)
            self.text = text

        def update(self, text: str) -> None:
            self.text = text

    class Static(Label):
        pass

    class Input(Widget):
        class Changed:
            def __init__(self, input, value: str):
                self.input = input
                self.value = value

        def __init__(self, *args: Any, placeholder: str = "", password: bool = False, **kwargs: Any):
            super().__init__(*args, **kwargs)
            self.placeholder = placeholder
            self.password = password
            self.value = ""

    class Button(Widget):
        class Pressed:
            def __init__(self, button):
                self.button = button

        def __init__(self, label: str = "", *args: Any, **kwargs: Any):
            super().__init__(*args, **kwargs)
            self.label = label

    class ListItem(Widget):
        pass

    class ListView(Widget):
        class Selected:
            def __init__(self, item):
                self.item = item

        def __init__(self, *args: Any, **kwargs: Any):
            super().__init__(*args, **kwargs)
            self.items: list[Any] = []

        def append(self, item) -> None:
            self.items.append(item)

        def clear(self) -> None:
            self.items.clear()

    class RichLog(Widget):
        def __init__(self, *args: Any, wrap: bool = False, **kwargs: Any):
            super().__init__(*args, **kwargs)
            self.wrap = wrap
            self.entries: list[Any] = []

        def write(self, value) -> None:
            self.entries.append(value)

        def clear(self) -> None:
            self.entries.clear()

    textual_module.on = on

    app_module.App = App
    app_module.ComposeResult = list

    containers_module.Horizontal = Horizontal
    containers_module.Vertical = Vertical
    containers_module.VerticalScroll = VerticalScroll

    widgets_module.DirectoryTree = DirectoryTree
    widgets_module.Button = Button
    widgets_module.Footer = Footer
    widgets_module.Header = Header
    widgets_module.Input = Input
    widgets_module.Label = Label
    widgets_module.ListItem = ListItem
    widgets_module.ListView = ListView
    widgets_module.RichLog = RichLog
    widgets_module.Static = Static

    screen_module.Screen = Screen

    sys.modules["textual"] = textual_module
    sys.modules["textual.app"] = app_module
    sys.modules["textual.screen"] = screen_module
    sys.modules["textual.containers"] = containers_module
    sys.modules["textual.widgets"] = widgets_module


def import_penrs_tui(force_reload: bool = False):
    install_textual_stub()

    if force_reload and "penrs_tui" in sys.modules:
        sys.modules.pop("penrs_tui", None)

    if "penrs_tui" in sys.modules:
        return sys.modules["penrs_tui"]
    return importlib.import_module("penrs_tui")
