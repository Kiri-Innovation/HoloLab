"""Load source without reading or writing a Python bytecode cache."""

from importlib.machinery import SourceFileLoader


class SourceOnlyLoader(SourceFileLoader):
    def get_code(self, fullname):
        return self.source_to_code(self.get_data(self.path), self.path)
